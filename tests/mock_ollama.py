"""A tiny fake Ollama server for tests (no GPU, no model needed)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading


class _Handler(BaseHTTPRequestHandler):
    calls: list = []
    plan_override: dict | None = None

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _answer(self, obj: dict):
        self._send(200, {"message": {"role": "assistant", "content": json.dumps(obj)},
                         "done": True, "done_reason": "stop", "prompt_eval_count": 100,
                         "eval_count": 50, "eval_duration": 500_000_000})

    def do_GET(self):
        if self.path == "/api/version":
            self._send(200, {"version": "0.12.0-mock"})
        elif self.path == "/api/tags":
            self._send(200, {"models": [{"name": "qwen2.5-coder:14b"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        _Handler.calls.append(req)
        system = req["messages"][0]["content"]
        prompt = req["messages"][-1]["content"]
        if "write the PLAN" in system:
            return self._plan(prompt)
        files = re.findall(r"===== FILE: (\S+) \(", prompt)
        findings = []
        for f in files:
            if f.endswith(".log"):
                findings.append({"file": f, "line": 3, "rule": "db-unreachable",
                                 "severity": "high", "category": "runtime",
                                 "message": "The app cannot reach PostgreSQL at db:5432.",
                                 "suggestion": "Point DATABASE_URL at the db Service.",
                                 "confidence": 0.9})
            if f.endswith("deploy.yaml"):
                findings.append({"file": f, "line": 9, "rule": "port-mismatch",
                                 "severity": "high", "category": "correctness",
                                 "message": "containerPort 8080 but the Service targets 80.",
                                 "suggestion": "Set targetPort: 8080.", "confidence": 0.85})
                findings.append({"file": f, "line": 1, "rule": "unsure", "severity": "low",
                                 "category": "style", "message": "maybe", "suggestion": "",
                                 "confidence": 0.1})                 # must be dropped
            findings.append({"file": "ghost/file.txt", "line": 1, "rule": "ghost",
                             "severity": "high", "category": "x", "message": "hallucinated",
                             "suggestion": "", "confidence": 0.9})   # must be dropped
        self._answer({"findings": findings})

    def _plan(self, prompt: str):
        if _Handler.plan_override is not None:
            return self._answer(_Handler.plan_override)
        ids = re.findall(r"^(F[0-9a-f]{8}) \[(\w+)\] (\S+?):", prompt, re.M)
        steps = []
        if ids:
            fid, sev, path = ids[0]
            steps.append({"title": "Fix the most important problem first", "why": "It breaks things.",
                          "priority": sev, "files": [path, "not/in/upload.txt"],
                          "findings": [fid, "Fdeadbeef"],
                          "actions": ["Edit the file.", "Redeploy."],
                          "commands": ["kubectl apply -f deploy.yaml"],
                          "patch": "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new",
                          "verify": ["kubectl get pods shows Running"], "effort": "minutes",
                          "risk": "Pods restart."})
        self._answer({"title": "Make it work", "summary": "Two things are broken.", "steps": steps})


class MockOllama:
    def __enter__(self):
        _Handler.calls = []
        _Handler.plan_override = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    @property
    def calls(self):
        return _Handler.calls

    def set_plan(self, plan: dict | None):
        _Handler.plan_override = plan

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
