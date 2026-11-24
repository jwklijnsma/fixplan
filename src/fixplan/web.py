"""`fixplan serve`: a small web UI. Upload files, a folder, a ZIP, or paste text; get a plan.

Standard library only. Jobs run one at a time on a background thread (one GPU), finished
plans are kept in ~/.cache/fixplan/jobs so they survive a restart.

Security:
- Binding to anything other than localhost needs a token (FIXPLAN_WEB_TOKEN or --token).
- Every API call needs the token when one is set; without one, cross-site requests are
  refused (Origin must match Host) so another web page cannot drive the API.
- Uploads are only read, never executed or written next to anything else.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import threading
import time
from urllib.parse import urlparse

from . import __version__
from .config import Config
from .intake import Intake, IntakeError
from .linters import available as linters_available
from .ollama import OllamaClient
from .planner import make_plan
from .report import to_json, to_markdown, to_sarif

JOB_ID = re.compile(r"^[a-f0-9]{16}$")
KEEP_JOBS = 100


class Job:
    def __init__(self, jid: str, name: str, goal: str):
        self.id = jid
        self.name = name
        self.goal = goal
        self.state = "queued"     # queued, running, done, failed
        self.progress = 0.0
        self.message = "waiting in the queue"
        self.created = time.time()
        self.finished: float | None = None
        self.plan: dict | None = None
        self.markdown = ""
        self.sarif = ""
        self.error = ""
        self.payload: tuple | None = None   # (files, notes, cfg) until it runs

    def summary(self) -> dict:
        p = self.plan or {}
        return {"id": self.id, "name": self.name, "goal": self.goal, "state": self.state,
                "progress": round(self.progress, 3), "message": self.message,
                "created": self.created, "finished": self.finished, "error": self.error,
                "steps": len(p.get("steps", [])), "findings": len(p.get("findings", [])),
                "title": p.get("title", "")}


class App:
    def __init__(self, cfg: Config, token: str, data_dir: Path):
        self.cfg = cfg
        self.token = token
        self.data_dir = data_dir
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self.stop = threading.Event()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._load()
        threading.Thread(target=self._worker, daemon=True, name="fixplan-worker").start()

    # -- persistence -------------------------------------------------------------------
    def _load(self) -> None:
        for d in sorted(self.data_dir.iterdir()) if self.data_dir.is_dir() else []:
            meta = d / "job.json"
            if not (JOB_ID.match(d.name) and meta.is_file()):
                continue
            try:
                m = json.loads(meta.read_text())
                job = Job(d.name, m.get("name", ""), m.get("goal", ""))
                job.__dict__.update({k: m[k] for k in ("state", "created", "finished", "error",
                                                       "message", "progress") if k in m})
                if job.state in ("queued", "running"):
                    job.state, job.error = "failed", "the server restarted while this job ran"
                if (d / "plan.json").is_file():
                    job.plan = json.loads((d / "plan.json").read_text())
                    job.markdown = (d / "plan.md").read_text() if (d / "plan.md").is_file() else ""
                    job.sarif = (d / "plan.sarif").read_text() if (d / "plan.sarif").is_file() else ""
                self.jobs[job.id] = job
            except (OSError, ValueError):
                continue

    def _save(self, job: Job) -> None:
        d = self.data_dir / job.id
        d.mkdir(exist_ok=True)
        meta = {k: getattr(job, k) for k in ("name", "goal", "state", "created", "finished",
                                             "error", "message", "progress")}
        (d / "job.json").write_text(json.dumps(meta))
        if job.plan is not None:
            (d / "plan.json").write_text(json.dumps(job.plan, ensure_ascii=False))
            (d / "plan.md").write_text(job.markdown)
            (d / "plan.sarif").write_text(job.sarif)

    def _prune(self) -> None:
        done = sorted((j for j in self.jobs.values() if j.state in ("done", "failed")),
                      key=lambda j: j.created)
        for j in done[: max(0, len(done) - KEEP_JOBS)]:
            self.delete(j.id)

    # -- jobs --------------------------------------------------------------------------
    def submit(self, body: dict) -> Job:
        intake = Intake(self.cfg)
        names = []
        for item in body.get("files") or []:
            if not isinstance(item, dict):
                continue
            try:
                data = base64.b64decode(item.get("data") or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntakeError(f"{item.get('name')}: bad upload encoding") from exc
            intake.add_bytes(str(item.get("name") or "upload"), data)
            names.append(str(item.get("name") or "upload"))
        text = body.get("text") or ""
        if isinstance(text, str) and text.strip():
            intake.add_text(text, str(body.get("text_name") or ""))
            names.append(str(body.get("text_name") or "pasted text"))
        files, notes = intake.result()
        cfg = replace(self.cfg)
        goal = str(body.get("goal") or "").strip()[:2000]
        if goal:
            cfg.goal = goal
        if body.get("language") in ("en", "nl"):
            cfg.language = body["language"]
        if body.get("no_llm"):
            cfg.use_llm = False
        name = names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}" if names else "upload"
        job = Job(secrets.token_hex(8), name[:120], goal)
        job.payload = (files, notes, cfg)
        with self.lock:
            self.jobs[job.id] = job
            self._prune()
        self._save(job)
        self.queue.put(job.id)
        return job

    def delete(self, jid: str) -> bool:
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job.state == "running":
                return False
            del self.jobs[jid]
        shutil.rmtree(self.data_dir / jid, ignore_errors=True)
        return True

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                jid = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            job = self.jobs.get(jid)
            if not job or job.payload is None:
                continue
            files, notes, cfg = job.payload
            job.state, job.message = "running", "starting"
            self._save(job)

            def progress(msg: str, frac: float, job=job):
                job.message, job.progress = msg, frac

            try:
                client = _client(cfg) if cfg.use_llm else None
                plan = make_plan(files, cfg, notes=notes, client=client, progress=progress,
                                 title_hint=f"Fix plan: {job.name}")
                job.plan = json.loads(to_json(plan))
                job.markdown = to_markdown(plan)
                job.sarif = to_sarif(plan)
                job.state, job.message, job.progress = "done", "done", 1.0
            except Exception as exc:   # noqa: BLE001 - a job must never kill the worker
                job.state, job.error = "failed", f"{exc.__class__.__name__}: {exc}"
            job.finished = time.time()
            job.payload = None
            self._save(job)


def _client(cfg: Config) -> OllamaClient:
    return OllamaClient(cfg.ollama_host, cfg.model, api_key=cfg.api_key, timeout=cfg.timeout,
                        retries=cfg.retries, temperature=cfg.temperature, num_ctx=cfg.num_ctx,
                        num_predict=cfg.num_predict, deadline=cfg.deadline,
                        keep_alive=cfg.keep_alive)


def make_handler(app: App):
    max_body = int(app.cfg.max_upload_mb * 1024 * 1024 * 1.4) + 65536   # base64 overhead

    class Handler(BaseHTTPRequestHandler):
        server_version = f"fixplan/{__version__}"

        def log_message(self, fmt, *args):
            if os.environ.get("FIXPLAN_ACCESS_LOG"):
                super().log_message(fmt, *args)

        # -- helpers -------------------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json")

        def _authorized(self) -> bool:
            if app.token:
                got = self.headers.get("Authorization", "")
                got = got[7:] if got.startswith("Bearer ") else self.headers.get("X-Fixplan-Token", "")
                return secrets.compare_digest(got.encode(), app.token.encode())
            # No token (localhost only): refuse cross-site requests.
            origin = self.headers.get("Origin")
            if origin:
                return urlparse(origin).netloc == self.headers.get("Host", "")
            return True

        # -- routes --------------------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                html = resources.files("fixplan").joinpath("static/index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8", {
                    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                               "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                               "frame-ancestors 'none'"})
            if path == "/healthz":
                return self._json(200, {"ok": True})
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._authorized():
                return self._json(401, {"error": "token required", "auth": bool(app.token)})
            if path == "/api/info":
                ok, err = True, ""
                try:
                    c = _client(app.cfg)
                    c.version()
                    have = c.has_model()
                except Exception as exc:   # noqa: BLE001
                    ok, have, err = False, False, str(exc)[:200]
                return self._json(200, {"version": __version__, "model": app.cfg.model,
                                        "ollama": app.cfg.ollama_host, "ollama_ok": ok,
                                        "model_ok": have, "error": err,
                                        "language": app.cfg.language,
                                        "max_upload_mb": app.cfg.max_upload_mb,
                                        "linters": {k: bool(v) for k, v in linters_available().items()}})
            if path == "/api/jobs":
                jobs = sorted(app.jobs.values(), key=lambda j: -j.created)
                return self._json(200, [j.summary() for j in jobs])
            m = re.match(r"^/api/jobs/([a-f0-9]{16})(?:/(plan\.md|plan\.json|plan\.sarif))?$", path)
            if not m or m.group(1) not in app.jobs:
                return self._json(404, {"error": "no such job"})
            job = app.jobs[m.group(1)]
            if not m.group(2):
                return self._json(200, {**job.summary(), "plan": job.plan})
            if job.plan is None:
                return self._json(409, {"error": "job not finished"})
            fname = m.group(2)
            body = {"plan.md": job.markdown, "plan.sarif": job.sarif,
                    "plan.json": json.dumps(job.plan, indent=2, ensure_ascii=False)}[fname]
            ctype = "text/markdown; charset=utf-8" if fname.endswith(".md") else "application/json"
            return self._send(200, body.encode(), ctype, {
                "Content-Disposition": f'attachment; filename="fixplan-{job.id}-{fname}"'})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized():
                return self._json(401, {"error": "token required", "auth": bool(app.token)})
            if path != "/api/jobs":
                return self._json(404, {"error": "not found"})
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._json(415, {"error": "send JSON"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > max_body:
                return self._json(413, {"error": f"upload larger than {app.cfg.max_upload_mb} MB"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected an object")
                job = app.submit(body)
            except (ValueError, IntakeError) as exc:
                return self._json(400, {"error": str(exc)})
            return self._json(202, job.summary())

        def do_DELETE(self):
            path = urlparse(self.path).path
            if not self._authorized():
                return self._json(401, {"error": "token required", "auth": bool(app.token)})
            m = re.match(r"^/api/jobs/([a-f0-9]{16})$", path)
            if not m:
                return self._json(404, {"error": "not found"})
            if app.delete(m.group(1)):
                return self._json(200, {"deleted": m.group(1)})
            return self._json(409, {"error": "no such job, or it is running"})

    return Handler


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(cfg: Config, bind: str = "127.0.0.1", port: int = 8766, token: str = "",
          data_dir: str | None = None, open_browser: bool = False,
          allow_no_token: bool = False) -> int:
    token = token or os.environ.get("FIXPLAN_WEB_TOKEN", "")
    if not token and not _is_loopback(bind) and not allow_no_token:
        print(f"fixplan serve: refusing to listen on {bind} without a token.\n"
              "  Set FIXPLAN_WEB_TOKEN (e.g. $(openssl rand -hex 24)) or pass --token,\n"
              "  or --no-token if something in front of it does authentication.")
        return 2
    ddir = Path(data_dir).expanduser() if data_dir else \
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fixplan" / "jobs"
    app = App(cfg, token, ddir)
    httpd = ThreadingHTTPServer((bind, port), make_handler(app))
    httpd.daemon_threads = True
    url = f"http://{'localhost' if _is_loopback(bind) else bind}:{httpd.server_address[1]}/"
    print(f"fixplan {__version__} web UI on {url}  (model {cfg.model} at {cfg.ollama_host})")
    if token:
        print("  token required: enter it in the UI, or open "
              f"{url}#token=<your token>")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        app.stop.set()
        httpd.server_close()
    return 0
