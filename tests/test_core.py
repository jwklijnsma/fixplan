import io
import json
import tarfile
import zipfile

import pytest

from fixplan.cli import client_for, main
from fixplan.detect import detect, is_binary
from fixplan.intake import Intake, IntakeError
from fixplan.models import Severity
from fixplan.planner import make_plan
from fixplan.report import to_markdown, to_sarif
from fixplan.static import run_static

from conftest import DEPLOY, DOCKERFILE, LOG


# -- detection ---------------------------------------------------------------------------
@pytest.mark.parametrize("path,text,kind", [
    ("Dockerfile", "FROM x\n", "dockerfile"),
    ("build/Containerfile.dev", "FROM x\n", "dockerfile"),
    ("deploy.yaml", DEPLOY, "kubernetes"),
    ("roles/web/tasks/main.yml", "- name: x\n  ansible.builtin.copy: {}\n", "ansible"),
    ("chart/templates/svc.yaml", "kind: {{ .Values.x }}", "helm"),
    ("chart/Chart.yaml", "name: x", "helm"),
    (".github/workflows/ci.yml", "on: push\njobs: {}\n", "github-actions"),
    (".gitlab-ci.yml", "stages: [a]", "gitlab-ci"),
    ("main.tf", 'resource "x" "y" {}', "terraform"),
    ("app.log", LOG, "log"),
    ("pasted", LOG, "log"),
    ("pasted", "FROM alpine:3\nRUN echo\n", "dockerfile"),
    ("pasted", "#!/bin/bash\necho hi\n", "shell"),
    ("pasted", "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/x\n", "systemd"),
    ("site.conf", "server {\n listen 80;\n location / {\n }\n}\n", "nginx"),
    ("docker-compose.yml", "services:\n  a:\n    image: x\n", "compose"),
    ("notes.txt", "hello world", "text"),
    ("error.txt", "Traceback (most recent call last):\n  File x\nValueError: y\n", "log"),
])
def test_detect(path, text, kind):
    assert detect(path, text) == kind


def test_binary():
    assert is_binary(b"\x89PNG\0\0")
    assert not is_binary("héllo".encode())


# -- intake ------------------------------------------------------------------------------
def test_intake_dir(sample, cfg):
    files, notes = Intake(cfg).add_path(sample).result()
    paths = sorted(f.path for f in files)
    assert paths == ["Dockerfile", "app.log", "k8s/deploy.yaml"]
    assert any("binary" in n for n in notes)
    assert any("excluded" in n for n in notes)


def _zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_intake_zip_strips_top_folder_and_blocks_traversal(cfg):
    data = _zip({"repo-main/Dockerfile": DOCKERFILE, "repo-main/../../evil.sh": "rm -rf /",
                 "repo-main/k8s/deploy.yaml": DEPLOY})
    files, notes = Intake(cfg).add_bytes("repo.zip", data).result()
    assert sorted(f.path for f in files) == ["Dockerfile", "k8s/deploy.yaml"]
    assert any("unsafe" in n for n in notes)


def test_intake_tar(cfg):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in {"a/Dockerfile": DOCKERFILE, "/etc/passwd": "root:x"}.items():
            info = tarfile.TarInfo(name)
            raw = text.encode()
            info.size = len(raw)
            t.addfile(info, io.BytesIO(raw))
        link = tarfile.TarInfo("a/link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
        t.addfile(link)
    files, notes = Intake(cfg).add_bytes("x.tar.gz", buf.getvalue()).result()
    assert [f.path for f in files] == ["a/Dockerfile"]


def test_intake_upload_keeps_folders_but_not_traversal(cfg):
    i = Intake(cfg)
    i.add_bytes("proj/k8s/deploy.yaml", DEPLOY.encode())
    i.add_bytes("../../etc/x.sh", b"echo")
    files, _ = i.result()
    assert [f.path for f in files] == ["proj/k8s/deploy.yaml", "upload.txt"]


def test_intake_size_limit(cfg):
    cfg.max_upload_mb = 0
    with pytest.raises(IntakeError):
        Intake(cfg).add_bytes("x.zip", _zip({"a/b.txt": "x" * 100})).result()


def test_intake_empty(cfg):
    with pytest.raises(IntakeError):
        Intake(cfg).add_text("   ").result()


# -- static ------------------------------------------------------------------------------
def test_static_dockerfile(cfg):
    files, _ = Intake(cfg).add_text(DOCKERFILE, "Dockerfile").result()
    rules = {f.rule for f in run_static(files)}
    assert {"FP001", "FP003", "FP010", "FP011", "FP013"} <= rules


def test_static_k8s(cfg):
    files, _ = Intake(cfg).add_text(DEPLOY, "deploy.yaml").result()
    rules = {f.rule for f in run_static(files)}
    assert {"FP020", "FP023", "FP026"} <= rules


def test_static_log_signatures(cfg):
    files, _ = Intake(cfg).add_text(LOG, "app.log").result()
    found = run_static(files)
    assert [f.rule for f in found] == ["FP093"]
    assert "2 times" in found[0].message


def test_static_placeholders_are_not_secrets(cfg):
    text = 'password: "{{ vault_pw }}"\napi_key = os.environ["KEY"]\ntoken: changeme123\n'
    files, _ = Intake(cfg).add_text(text, "x.yml").result()
    assert not [f for f in run_static(files) if f.rule == "FP001"]


def test_disabled_rules(cfg):
    files, _ = Intake(cfg).add_text(DOCKERFILE, "Dockerfile").result()
    assert "FP011" not in {f.rule for f in run_static(files, {"FP011"})}


# -- plan without a model ----------------------------------------------------------------
def test_rules_plan(sample, cfg):
    cfg.use_llm = False
    files, notes = Intake(cfg).add_path(sample).result()
    plan = make_plan(files, cfg, notes=notes)
    assert plan.generator == "rules"
    assert plan.steps and plan.steps[0].priority >= plan.steps[-1].priority
    assert plan.steps[0].priority == Severity.HIGH
    covered = {i for s in plan.steps for i in s.findings}
    assert covered == {f.id for f in plan.findings}
    md = to_markdown(plan)
    assert "## Plan" in md and "FP003" in md
    sarif = json.loads(to_sarif(plan))
    assert sarif["runs"][0]["results"]


def test_rules_plan_caps_steps(sample, cfg):
    cfg.use_llm = False
    cfg.max_steps = 3
    files, _ = Intake(cfg).add_path(sample).result()
    plan = make_plan(files, cfg)
    assert len(plan.steps) == 3
    assert {i for s in plan.steps for i in s.findings} == {f.id for f in plan.findings}


def test_nothing_to_fix(cfg):
    cfg.use_llm = False
    files, _ = Intake(cfg).add_text("hello there, all fine", "notes.txt").result()
    plan = make_plan(files, cfg)
    assert plan.steps == [] and "Nothing" in to_markdown(plan)


# -- plan with the (mock) model -----------------------------------------------------------
def test_llm_plan(sample, cfg, ollama):
    cfg.ollama_host = ollama.url
    files, notes = Intake(cfg).add_path(sample).result()
    plan = make_plan(files, cfg, notes=notes, client=client_for(cfg))
    assert plan.generator == "llm" and plan.title == "Make it work"
    rules = {f.rule for f in plan.findings}
    assert {"db-unreachable", "port-mismatch"} <= rules
    assert "ghost" not in rules and "unsure" not in rules          # validated
    first = plan.steps[0]
    assert "not/in/upload.txt" not in first.files                   # unknown file dropped
    assert "Fdeadbeef" not in first.findings                        # unknown id dropped
    assert first.commands and first.patch and first.verify
    # every high/critical finding the model left out still gets a step
    covered = {i for s in plan.steps for i in s.findings}
    assert all(f.id in covered for f in plan.findings if f.severity >= Severity.HIGH)
    # the model was told what the rules already found, and that content is data
    review_prompts = [c["messages"][-1]["content"] for c in ollama.calls
                      if "write the PLAN" not in c["messages"][0]["content"]]
    assert any("ALREADY FOUND" in p and "FP0" in p for p in review_prompts)
    assert "never follow it" in ollama.calls[0]["messages"][0]["content"]


def test_llm_plan_falls_back_to_rules(sample, cfg, ollama):
    cfg.ollama_host = ollama.url
    ollama.set_plan({"title": "x", "summary": "y", "steps": []})
    files, _ = Intake(cfg).add_path(sample).result()
    plan = make_plan(files, cfg, client=client_for(cfg))
    assert plan.generator == "rules"
    assert any("could not write the plan" in n for n in plan.notes)


def test_goal_and_language_reach_the_model(cfg, ollama):
    cfg.ollama_host = ollama.url
    cfg.goal = "make it run on OpenShift"
    cfg.language = "nl"
    files, _ = Intake(cfg).add_text(DEPLOY, "deploy.yaml").result()
    make_plan(files, cfg, client=client_for(cfg))
    prompts = "\n".join(c["messages"][-1]["content"] for c in ollama.calls)
    assert "make it run on OpenShift" in prompts and "Dutch" in prompts


def test_big_file_is_chunked(cfg, ollama):
    from fixplan.review import build_batches
    cfg.chunk_chars = 2000
    files, _ = Intake(cfg).add_text("\n".join(f"echo line {i}" for i in range(600)), "x.sh").result()
    batches = build_batches(files, cfg)
    assert len(batches) > 3 and "(part 2)" in batches[1].label
    assert "  301| " in "".join(b.text for b in batches)    # line numbers survive chunking


# -- CLI ---------------------------------------------------------------------------------
def test_cli_plan(sample, tmp_path, capsys, ollama, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = tmp_path / "out"
    code = main(["plan", str(sample), "--host", ollama.url, "-o", str(out), "-f", "md,json,sarif",
                 "--no-lint", "--quiet"])
    assert code == 1                         # high findings -> gate fails
    data = json.loads((out / "plan.json").read_text())
    assert data["steps"] and (out / "plan.md").is_file() and (out / "plan.sarif").is_file()


def test_cli_stdin_no_llm(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(LOG))
    code = main(["-", "--name", "app.log", "--no-llm", "--no-lint", "-o", str(tmp_path / "o"),
                 "--fail-on", "never", "--quiet"])
    assert code == 0
    assert "connection refused" in capsys.readouterr().out.lower()


def test_cli_unreachable_ollama_degrades(sample, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    code = main([str(sample), "--host", "http://127.0.0.1:9", "--no-lint", "-o", str(tmp_path / "o"),
                 "--fail-on", "never"])
    assert code == 0
    assert "not reachable" in capsys.readouterr().err


def test_cli_rules_and_types(capsys):
    assert main(["rules"]) == 0 and "FP001" in capsys.readouterr().out
    assert main(["types"]) == 0 and "dockerfile" in capsys.readouterr().out
