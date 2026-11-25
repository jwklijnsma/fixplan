"""linters.py against fake linter executables, and the streaming Ollama client
against a scripted streaming server."""
from __future__ import annotations

import json
import stat
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from fixplan import linters, ollama
from fixplan.models import Severity, SourceFile
from fixplan.ollama import OllamaClient, OllamaError, OllamaTimeout, parse_json_lenient


# --------------------------------------------------------------------------- linters

@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Fake linters on PATH. Each prints <state>/<name>.out and records argv/cwd/files."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    for name in ("shellcheck", "hadolint", "ruff", "yamllint"):
        exe = bin_ / name
        exe.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, os, pathlib, sys
            d = pathlib.Path({str(state)!r})
            calls = d / "{name}.calls"
            seen = json.loads(calls.read_text()) if calls.exists() else []
            files = sorted(str(p.relative_to(os.getcwd())) for p in pathlib.Path(os.getcwd()).rglob("*") if p.is_file())
            seen.append({{"argv": sys.argv[1:], "files": files, "stdin_tty": sys.stdin.isatty()}})
            calls.write_text(json.dumps(seen))
            if (d / "{name}.crash").exists():
                sys.exit(1)
            out = d / "{name}.out"
            sys.stdout.write(out.read_text() if out.exists() else "")
            """))
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_))

    class T:
        def out(self, name, text):
            (state / f"{name}.out").write_text(text)

        def calls(self, name):
            p = state / f"{name}.calls"
            return json.loads(p.read_text()) if p.exists() else []

        def remove(self, name):
            (bin_ / name).unlink()
    return T()


FILES = [
    SourceFile("scripts/deploy.sh", "shell", "#!/bin/sh\necho $1\n"),
    SourceFile("Dockerfile", "dockerfile", "FROM ubuntu\nRUN apt-get install curl\n"),
    SourceFile("app/main.py", "python", "import os\n"),
    SourceFile("k8s/deploy.yaml", "kubernetes", "kind: Deployment\n"),
    SourceFile(".gitlab-ci.yml", "gitlab-ci", "stages: [x]\n"),
    SourceFile("app.log", "log", "ERROR boom\n"),
]


def test_every_linter_runs_on_its_files_from_a_private_copy(tools):
    tools.out("shellcheck", json.dumps([{"code": 2086, "level": "info", "file": "scripts/deploy.sh", "line": 2,
                                         "message": "Double quote to prevent globbing."}]))
    tools.out("hadolint", json.dumps([{"code": "DL3008", "level": "warning", "line": 2, "message": "Pin versions"}]))
    tools.out("ruff", json.dumps([]))
    tools.out("yamllint", "k8s/deploy.yaml:1:1: [error] syntax error: bad (syntax)\n"
                          ".gitlab-ci.yml:3:5: [warning] truthy value (truthy)\nnoise line\n")
    findings, notes = linters.run_linters(FILES)
    assert notes == []
    got = {(f.source, f.rule, f.severity, f.file, f.line) for f in findings}
    assert got == {
        ("shellcheck", "SC2086", Severity.LOW, "scripts/deploy.sh", 2),
        ("hadolint", "DL3008", Severity.MEDIUM, "Dockerfile", 2),
        ("yamllint", "yamllint", Severity.MEDIUM, "k8s/deploy.yaml", 1),
        ("yamllint", "yamllint", Severity.LOW, ".gitlab-ci.yml", 3),
    }
    sc = next(f for f in findings if f.source == "shellcheck")
    assert sc.suggestion == "https://www.shellcheck.net/wiki/SC2086"
    # each tool only gets its own kinds; the log file is never handed to a linter
    assert tools.calls("shellcheck")[0]["argv"][-1] == "scripts/deploy.sh"
    assert tools.calls("hadolint")[0]["argv"] == ["-f", "json", "--no-fail", "Dockerfile"]
    assert tools.calls("yamllint")[0]["argv"][-2:] == ["k8s/deploy.yaml", ".gitlab-ci.yml"]
    assert "--select" in tools.calls("ruff")[0]["argv"] and "E9,F,B,S" in tools.calls("ruff")[0]["argv"]
    # the private copy holds the upload's text files, and stdin is closed
    assert "app.log" in tools.calls("ruff")[0]["files"] and not tools.calls("ruff")[0]["stdin_tty"]


def test_ruff_severities_and_relative_paths(tools, tmp_path):
    def ruff_out(code, row=1):
        return {"code": code, "filename": "__ROOT__/app/main.py", "location": {"row": row}, "message": code}

    # The fake can't know the temp root, so make paths relative to cwd via a placeholder rewrite.
    (tmp_path / "bin" / "ruff").write_text((tmp_path / "bin" / "ruff").read_text().replace(
        'sys.stdout.write(out.read_text() if out.exists() else "")',
        'sys.stdout.write((out.read_text() if out.exists() else "").replace("__ROOT__", os.getcwd()))'))
    tools.out("ruff", json.dumps([ruff_out("F821"), ruff_out("S602", 2), ruff_out("F401", 3), ruff_out(None, 4)]))
    findings, _ = linters.run_linters([FILES[2]])
    assert [(f.rule, f.severity, f.file, f.line, f.category) for f in findings] == [
        ("F821", Severity.HIGH, "app/main.py", 1, "correctness"),
        ("S602", Severity.MEDIUM, "app/main.py", 2, "security"),
        ("F401", Severity.LOW, "app/main.py", 3, "correctness"),
        ("ruff", Severity.LOW, "app/main.py", 4, "correctness"),
    ]


def test_missing_and_broken_linters_become_notes(tools):
    tools.remove("hadolint")
    tools.out("shellcheck", "not json")
    findings, notes = linters.run_linters(FILES[:2])
    assert findings == []
    assert "hadolint is not installed: 1 file(s) reviewed without it" in notes
    assert any(n.startswith("shellcheck failed:") for n in notes)


def test_nothing_to_lint(tools):
    assert linters.run_linters([FILES[-1]]) == ([], [])
    assert tools.calls("ruff") == []


def test_available_reports_each_tool(tools):
    tools.remove("ruff")
    av = linters.available()
    assert av["ruff"] is None and av["shellcheck"].endswith("/shellcheck")


# --------------------------------------------------------------------------- ollama client

class Script:
    """Each POST /api/chat pops one step: (status, [json lines]) or (status, "raw body")."""

    def __init__(self):
        self.steps, self.requests = [], []


@pytest.fixture
def server():
    script = Script()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = {"/api/version": {"version": "0.12.3"},
                    "/api/tags": {"models": [{"name": "qwen2.5-coder:14b"}, {"name": "llama3:latest"}]},
                    "/api/ps": {"models": [{"name": "qwen2.5-coder:14b", "size_vram": 1}]}}.get(self.path)
            data = json.dumps(body or {}).encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            script.requests.append({"body": json.loads(self.rfile.read(n)), "auth": self.headers.get("Authorization")})
            status, payload = script.steps.pop(0) if script.steps else (200, [{"message": {"content": "{}"}, "done": True}])
            data = payload.encode() if isinstance(payload, str) else \
                "".join(json.dumps(x) + "\n" for x in payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    script.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield script
    srv.shutdown()


def chunks(*parts, done_reason="stop"):
    lines = [{"message": {"content": p}} for p in parts]
    lines.append({"message": {"content": ""}, "done": True, "done_reason": done_reason,
                  "prompt_eval_count": 120, "eval_count": 30, "eval_duration": 1})
    return lines


def test_health_endpoints(server):
    c = OllamaClient(server.url, "qwen2.5-coder:14b")
    assert c.version() == "0.12.3"
    assert c.models() == ["qwen2.5-coder:14b", "llama3:latest"]
    assert c.has_model() and OllamaClient(server.url, "llama3").has_model()
    assert not OllamaClient(server.url, "mistral").has_model()
    assert c.running()[0]["name"] == "qwen2.5-coder:14b"


def test_streamed_json_is_joined_and_counted(server):
    server.steps = [(200, chunks('{"find', 'ings": [', ']}'))]
    c = OllamaClient(server.url, "m", api_key="sk-x", num_ctx=8192, num_predict=512, keep_alive="5m")
    assert c.chat_json("sys", "user", {"type": "object"}) == {"findings": []}
    assert (c.tokens_in, c.tokens_out) == (120, 30) and c.last_stats["eval_count"] == 30
    req = server.requests[0]
    assert req["auth"] == "Bearer sk-x"
    assert req["body"]["format"] == {"type": "object"} and req["body"]["stream"] is True
    assert req["body"]["options"] == {"temperature": 0.1, "num_ctx": 8192, "num_predict": 512}
    assert req["body"]["keep_alive"] == "5m"


def test_old_ollama_without_schemas_falls_back_to_json_mode(server):
    server.steps = [(400, '{"error": "invalid format: expected \\"json\\""}'), (200, chunks('{"a": 1}'))]
    c = OllamaClient(server.url, "m")
    assert c.chat_json("s", "u", {"type": "object"}) == {"a": 1}
    assert server.requests[1]["body"]["format"] == "json"
    server.steps = [(200, chunks('{"b": 2}'))]
    c.chat_json("s", "u", {"type": "object"})
    assert server.requests[2]["body"]["format"] == "json"          # remembered


def test_model_missing_and_stream_errors_are_not_retried(server):
    server.steps = [(404, '{"error": "model not found"}')]
    with pytest.raises(OllamaError, match="ollama pull m"):
        OllamaClient(server.url, "m").chat_json("s", "u", {})
    server.steps = [(200, [{"message": {"content": "{"}}, {"error": "CUDA out of memory"}])]
    with pytest.raises(OllamaError, match="CUDA out of memory"):
        OllamaClient(server.url, "m").chat_json("s", "u", {})
    assert len(server.requests) == 2


def test_hitting_num_predict_is_a_timeout(server):
    server.steps = [(200, chunks('{"findings": [', done_reason="length"))]
    with pytest.raises(OllamaTimeout, match="num_predict=64"):
        OllamaClient(server.url, "m", num_predict=64).chat_json("s", "u", {})


def test_bad_json_is_retried_then_gives_up(server, monkeypatch):
    monkeypatch.setattr(ollama.time, "sleep", lambda s: None)
    server.steps = [(200, chunks("no json here")), (500, "{}"), (200, chunks('{"ok": true}'))]
    assert OllamaClient(server.url, "m", retries=2).chat_json("s", "u", {}) == {"ok": True}
    server.steps = [(500, "{}")] * 3
    with pytest.raises(OllamaError, match="after 2 attempts"):
        OllamaClient(server.url, "m", retries=1).chat_json("s", "u", {})


def test_deadline_caps_a_slow_stream(server, monkeypatch):
    t = iter(range(0, 10_000, 50))
    monkeypatch.setattr(ollama.time, "monotonic", lambda: next(t))
    server.steps = [(200, [{"message": {"content": "x"}}] * 5 + chunks("y"))]
    with pytest.raises(OllamaTimeout, match="exceeded 100s"):
        OllamaClient(server.url, "m", timeout=50).chat_json("s", "u", {})


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Sure! Here you go: {"a": {"b": 2}} hope that helps', {"a": {"b": 2}}),
    ('[{"rule": "x"}]', {"findings": [{"rule": "x"}]}),
])
def test_parse_json_lenient(text, expected):
    assert parse_json_lenient(text) == expected


@pytest.mark.parametrize("text", ["no braces", "42", '"just a string"'])
def test_parse_json_lenient_rejects(text):
    with pytest.raises(ValueError):
        parse_json_lenient(text)
