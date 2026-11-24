"""`fixplan serve`: the web UI. Upload files, a folder or an archive, or paste text; get a plan.

Standard library only; the React UI in web/ is built into src/fixplan/static/ and committed.

* Locked down by default, like ansreview and helmreview: every API call needs the access
  token. It is printed as a link (`/?token=...`), which swaps it for an HttpOnly,
  SameSite=Strict cookie. Scripts can send `Authorization: Bearer <token>` instead.
* Every POST/DELETE from a browser needs the `X-Fixplan: 1` header, which another web page
  cannot send without a CORS preflight that this server never allows (CSRF).
* Jobs run one at a time on a background thread (one GPU). Finished plans, their log and
  the uploaded text (for code snippets) live in ~/.cache/fixplan/jobs, the newest 100.
* Uploads are only read: never executed, never written next to anything else.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
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
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import Config
from .intake import Intake, IntakeError
from .linters import available as linters_available
from .ollama import OllamaClient
from .planner import make_plan
from .report import to_json, to_markdown, to_sarif

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8"),
                "/favicon.svg": ("favicon.svg", "image/svg+xml")}
COOKIE = "fixplan_token"
CSRF_HEADER = "X-Fixplan"
JOB_ID = re.compile(r"^[a-f0-9]{16}$")
KEEP_JOBS = 100
MAX_LOG = 400
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
PLACEHOLDER_TOKENS = {"change-me", "changeme", "change_me", "secret", "token"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, jid: str, name: str, goal: str, rules_only: bool = False):
        self.id = jid
        self.name = name
        self.goal = goal
        self.rules_only = rules_only
        self.status = "queued"      # queued, running, done, failed, cancelled
        self.pct = 0.0
        self.message = "waiting in the queue"
        self.created = time.time()
        self.started: float | None = None
        self.ended: float | None = None
        self.error = ""
        self.model = ""
        self.lines: list[str] = []
        self.plan: dict | None = None
        self.markdown = ""
        self.sarif = ""
        self.files: dict[str, str] | None = None   # path -> text (loaded lazily from disk)
        self.payload: tuple | None = None           # (files, notes, cfg) until it runs
        self.cancel = threading.Event()

    @property
    def seconds(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or time.time()) - self.started

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.lines.append(f"{stamp} {text}")
        del self.lines[:-MAX_LOG]

    def summary(self, position: int | None = None) -> dict:
        p = self.plan or {}
        counts: dict[str, int] = {}
        for f in p.get("findings", []):
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        return {"id": self.id, "name": self.name, "goal": self.goal, "status": self.status,
                "rules_only": self.rules_only,
                "progress": {"pct": round(self.pct, 3), "message": self.message},
                "created": self.created, "started": self.started, "ended": self.ended,
                "seconds": round(self.seconds, 1), "error": self.error, "position": position,
                "model": p.get("model") or self.model, "title": p.get("title", ""),
                "steps": len(p.get("steps", [])), "findings": len(p.get("findings", [])),
                "files": len(p.get("files", [])), "counts": counts if self.plan else None}


class App:
    def __init__(self, cfg: Config, token: str | None, data_dir: Path):
        self.cfg = cfg
        self.default_model = cfg.model
        self.model_override = ""
        self.token = token
        self.data_dir = data_dir
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []          # queued job ids, in order
        self.lock = threading.Lock()
        self.queue: queue.Queue[str] = queue.Queue()
        self.stop = threading.Event()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._load()
        threading.Thread(target=self._worker, daemon=True, name="fixplan-worker").start()

    @property
    def model(self) -> str:
        return self.model_override or self.default_model

    def check_token(self, candidate: str | None) -> bool:
        if self.token is None:
            return True
        return bool(candidate) and hmac.compare_digest(candidate.encode(), self.token.encode())

    # -- persistence -------------------------------------------------------------------
    def _load(self) -> None:
        for d in sorted(self.data_dir.iterdir()):
            meta = d / "job.json"
            if not (JOB_ID.match(d.name) and meta.is_file()):
                continue
            try:
                m = json.loads(meta.read_text())
                job = Job(d.name, m.get("name", ""), m.get("goal", ""), m.get("rules_only", False))
                for k in ("status", "created", "started", "ended", "error", "message", "pct",
                          "model", "lines"):
                    if k in m:
                        setattr(job, k, m[k])
                if job.status in ("queued", "running"):
                    job.status, job.error = "failed", "the server restarted while this job ran"
                if (d / "plan.json").is_file():
                    job.plan = json.loads((d / "plan.json").read_text())
                    job.markdown = _read(d / "plan.md")
                    job.sarif = _read(d / "plan.sarif")
                self.jobs[job.id] = job
            except (OSError, ValueError):
                continue

    def _save(self, job: Job) -> None:
        d = self.data_dir / job.id
        d.mkdir(exist_ok=True)
        meta = {k: getattr(job, k) for k in ("name", "goal", "rules_only", "status", "created",
                                             "started", "ended", "error", "pct", "model",
                                             "lines")}
        meta["message"] = job.message
        _write(d / "job.json", json.dumps(meta))
        if job.plan is not None:
            _write(d / "plan.json", json.dumps(job.plan, ensure_ascii=False))
            _write(d / "plan.md", job.markdown)
            _write(d / "plan.sarif", job.sarif)

    def file_text(self, job: Job, path: str) -> str:
        if job.files is None:
            try:
                job.files = json.loads((self.data_dir / job.id / "files.json").read_text())
            except (OSError, ValueError):
                job.files = {}
        if path not in job.files:
            raise ApiError(404, "no such file in this upload")
        return job.files[path]

    def _prune(self) -> None:
        done = sorted((j for j in self.jobs.values() if j.status not in ("queued", "running")),
                      key=lambda j: j.created)
        for j in done[: max(0, len(done) - KEEP_JOBS)]:
            self.delete(j.id)

    # -- jobs --------------------------------------------------------------------------
    def position(self, jid: str) -> int | None:
        with self.lock:
            return self.order.index(jid) + 1 if jid in self.order else None

    def submit(self, body: dict) -> Job:
        intake = Intake(self.cfg)
        names = []
        for item in body.get("files") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "upload")
            try:
                data = base64.b64decode(item.get("data") or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise IntakeError(f"{name}: bad upload encoding") from exc
            intake.add_bytes(name, data)
            names.append(name)
        text = body.get("text") or ""
        if isinstance(text, str) and text.strip():
            intake.add_text(text, str(body.get("text_name") or ""))
            names.append(str(body.get("text_name") or "pasted text"))
        files, notes = intake.result()
        cfg = replace(self.cfg, model=self.model)
        goal = str(body.get("goal") or "").strip()[:2000]
        if goal:
            cfg.goal = goal
        if body.get("language") in ("en", "nl"):
            cfg.language = body["language"]
        if body.get("no_llm"):
            cfg.use_llm = False
        top = sorted({n.replace("\\", "/").split("/")[0] for n in names})
        name = top[0] if len(top) == 1 else f"{top[0]} +{len(top) - 1}" if top else "upload"
        job = Job(secrets.token_hex(8), name[:120], goal, rules_only=not cfg.use_llm)
        job.model = "" if not cfg.use_llm else cfg.model
        job.payload = (files, notes, cfg)
        kinds: dict[str, int] = {}
        for f in files:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        job.log(f"received {len(files)} file(s): " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
        for n in notes:
            job.log(f"note: {n}")
        job.files = {f.path: f.text for f in files}
        d = self.data_dir / job.id
        d.mkdir(parents=True, exist_ok=True)
        _write(d / "files.json", json.dumps(job.files, ensure_ascii=False))
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            self._prune()
        self._save(job)
        self.queue.put(job.id)
        return job

    def cancel(self, jid: str) -> Job:
        job = self.jobs.get(jid)
        if not job:
            raise ApiError(404, "no such job")
        if job.status not in ("queued", "running"):
            raise ApiError(409, "the job is not running")
        job.cancel.set()
        if job.status == "queued":
            with self.lock:
                if jid in self.order:
                    self.order.remove(jid)
            job.status, job.message, job.ended = "cancelled", "cancelled", time.time()
            job.payload = None
            job.log("cancelled before it started")
            self._save(job)
        return job

    def delete(self, jid: str) -> None:
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                raise ApiError(404, "no such job")
            if job.status in ("queued", "running"):
                raise ApiError(409, "cancel the job first")
            del self.jobs[jid]
        shutil.rmtree(self.data_dir / jid, ignore_errors=True)

    def _worker(self) -> None:
        while not self.stop.is_set():
            try:
                jid = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.lock:
                if jid in self.order:
                    self.order.remove(jid)
            job = self.jobs.get(jid)
            if not job or job.payload is None or job.status != "queued":
                continue
            files, notes, cfg = job.payload
            job.status, job.started, job.message = "running", time.time(), "starting"
            job.log(f"started{' (rules only)' if not cfg.use_llm else ' with ' + cfg.model}")
            self._save(job)

            def progress(msg: str, frac: float, job=job):
                if job.cancel.is_set():
                    raise Cancelled()
                if msg != job.message:
                    job.log(msg)
                job.message, job.pct = msg, frac

            try:
                client = None
                if cfg.use_llm:
                    client = _client(cfg)
                    try:
                        client.version()
                    except Exception as exc:   # noqa: BLE001
                        job.log(f"warning: Ollama not reachable at {cfg.ollama_host} "
                                f"({exc.__class__.__name__}); rules and linters only")
                        cfg.use_llm, client = False, None
                plan = make_plan(files, cfg, notes=notes, client=client, progress=progress,
                                 title_hint=f"Fix plan: {job.name}")
                job.plan = json.loads(to_json(plan))
                job.markdown = to_markdown(plan)
                job.sarif = to_sarif(plan)
                job.status, job.message, job.pct = "done", "done", 1.0
                job.log(f"✓ {len(plan.steps)} step(s), {len(plan.findings)} finding(s)")
            except Cancelled:
                job.status, job.message = "cancelled", "cancelled"
                job.log("cancelled")
            except Exception as exc:   # noqa: BLE001 - a job must never kill the worker
                job.status, job.error = "failed", f"{exc.__class__.__name__}: {exc}"
                job.log(f"error: {job.error}")
            job.ended = time.time()
            job.payload = None
            self._save(job)

    # -- status ------------------------------------------------------------------------
    def status(self) -> dict:
        c = _client(replace(self.cfg, model=self.model))
        ollama, err, models = None, "", []
        try:
            ollama = c.version()
            models = sorted(c.models())
        except Exception as exc:   # noqa: BLE001
            err = str(exc)[:300]
        want = self.model if ":" in self.model else self.model + ":latest"
        return {"version": __version__, "ollama_host": self.cfg.ollama_host, "ollama": ollama,
                "ollama_error": err, "model": self.model, "default_model": self.default_model,
                "model_source": "ui" if self.model_override else "config",
                "model_available": (want in models or self.model in models) if ollama else None,
                "models": models, "language": self.cfg.language,
                "linters": {k: bool(v) for k, v in linters_available().items()},
                "auth": self.token is not None, "max_upload_mb": self.cfg.max_upload_mb}


def _read(p: Path) -> str:
    return p.read_text() if p.is_file() else ""


def _write(p: Path, text: str) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(p)


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

        # -- responses -----------------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, data, extra: dict | None = None) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8", extra)

        # -- auth ----------------------------------------------------------------------
        def _token(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:].strip()
            jar = cookies.SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except cookies.CookieError:
                return None
            return jar[COOKIE].value if COOKIE in jar else None

        def _cookie_header(self, token: str) -> str:
            return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict"

        def _body(self, limit: int = 65536) -> dict:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(415, "send JSON")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > limit:
                raise ApiError(413, f"upload larger than {app.cfg.max_upload_mb} MB"
                               if limit > 65536 else "request too large")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as exc:
                raise ApiError(400, "bad JSON") from exc
            if not isinstance(data, dict):
                raise ApiError(400, "expected a JSON object")
            return data

        # -- routing -------------------------------------------------------------------
        def do_HEAD(self):
            self._route("GET")

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method: str):
            url = urlparse(self.path)
            path, qs = url.path, parse_qs(url.query)
            try:
                if method == "GET" and path in STATIC_FILES:
                    return self._static(path, qs)
                if path == "/healthz":
                    return self._json(200, {"ok": True})
                if not path.startswith("/api/"):
                    raise ApiError(404, "not found")
                if method != "GET" and not (self.headers.get(CSRF_HEADER) == "1"
                                            or self.headers.get("Authorization", "").startswith("Bearer ")):
                    raise ApiError(403, f"missing {CSRF_HEADER} header")
                if path == "/api/login" and method == "POST":
                    return self._login()
                if not app.check_token(self._token()):
                    raise ApiError(401, "access token required")
                return self._api(method, path, qs)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})
            except IntakeError as exc:
                self._json(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _static(self, path: str, qs: dict):
            name, ctype = STATIC_FILES[path]
            token = (qs.get("token") or [None])[0]
            if path in ("/", "/index.html") and token:
                # Trade the token in the URL for a cookie and drop it from the address bar.
                if not app.check_token(token):
                    raise ApiError(401, "wrong access token")
                return self._send(302, b"", "text/plain", {"Location": "/",
                                                           "Set-Cookie": self._cookie_header(token)})
            f = STATIC_DIR / name
            if not f.is_file():
                raise ApiError(404, "the web UI is not built: run `make web`")
            extra = {"Content-Security-Policy": CSP} if name == "index.html" else {}
            self._send(200, f.read_bytes(), ctype, extra)

        def _login(self):
            token = str(self._body().get("token") or "").strip()
            if not app.check_token(token):
                time.sleep(0.5)
                raise ApiError(401, "wrong access token")
            extra = {"Set-Cookie": self._cookie_header(token)} if app.token else {}
            self._json(200, {"ok": True}, extra)

        def _api(self, method: str, path: str, qs: dict):
            if path == "/api/status" and method == "GET":
                return self._json(200, app.status())
            if path == "/api/settings" and method == "POST":
                model = str(self._body().get("model") or "").strip()
                if model and not MODEL_NAME.match(model):
                    raise ApiError(400, "not a valid model name")
                app.model_override = "" if model == app.default_model else model
                return self._json(200, {"model": app.model,
                                        "model_source": "ui" if app.model_override else "config"})
            if path == "/api/jobs" and method == "GET":
                jobs = sorted(app.jobs.values(), key=lambda j: -j.created)
                return self._json(200, {"jobs": [j.summary(app.position(j.id)) for j in jobs]})
            if path == "/api/jobs" and method == "POST":
                job = app.submit(self._body(max_body))
                return self._json(202, job.summary(app.position(job.id)))
            m = re.match(r"^/api/jobs/([a-f0-9]{16})(?:/([a-z.]+))?$", path)
            if not m or m.group(1) not in app.jobs:
                raise ApiError(404, "no such job")
            job, sub = app.jobs[m.group(1)], m.group(2)
            if sub is None and method == "GET":
                since = int((qs.get("since") or ["0"])[0] or 0)
                return self._json(200, {**job.summary(app.position(job.id)), "plan": job.plan,
                                        "lines": job.lines[since:], "next": len(job.lines)})
            if (sub is None and method == "DELETE") or (sub == "delete" and method == "POST"):
                app.delete(job.id)
                return self._json(200, {"deleted": job.id})
            if sub == "cancel" and method == "POST":
                app.cancel(job.id)
                return self._json(200, job.summary())
            if sub == "file" and method == "GET":
                p = (qs.get("path") or [""])[0]
                return self._json(200, {"path": p, "content": app.file_text(job, p)})
            if sub in ("plan.md", "plan.json", "plan.sarif") and method == "GET":
                if job.plan is None:
                    raise ApiError(409, "the plan is not ready")
                body = {"plan.md": job.markdown, "plan.sarif": job.sarif,
                        "plan.json": json.dumps(job.plan, indent=2, ensure_ascii=False)}[sub]
                ctype = "text/markdown; charset=utf-8" if sub.endswith(".md") else "application/json"
                return self._send(200, body.encode(), ctype, {
                    "Content-Disposition": f'attachment; filename="fixplan-{job.id}-{sub}"'})
            raise ApiError(404, "not found")

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
          no_auth: bool = False) -> int:
    if no_auth:
        tok = None
        if not _is_loopback(bind):
            print(f"warning: --no-token on {bind}: anyone who can reach this port can use it. "
                  "Only do this behind a proxy that authenticates.")
    else:
        tok = token or os.environ.get("FIXPLAN_WEB_TOKEN") or secrets.token_urlsafe(24)
        if tok.strip().lower() in PLACEHOLDER_TOKENS:
            print(f"error: the access token is still the example value '{tok}'; set your own "
                  "(openssl rand -hex 24).")
            return 2
    ddir = Path(data_dir).expanduser() if data_dir else \
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fixplan" / "jobs"
    app = App(cfg, tok, ddir)
    httpd = ThreadingHTTPServer((bind, port), make_handler(app))
    httpd.daemon_threads = True
    host = "localhost" if _is_loopback(bind) or bind in ("0.0.0.0", "::") else bind
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"fixplan {__version__} web UI (model {cfg.model} at {cfg.ollama_host})")
    if tok and not (token or os.environ.get("FIXPLAN_WEB_TOKEN")):
        print("  access token (new on every start; set FIXPLAN_WEB_TOKEN to keep one):")
    print(f"  open: {url}?token={tok}" if tok else f"  open: {url}")
    if open_browser:
        import webbrowser
        webbrowser.open(f"{url}?token={tok}" if tok else url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        app.stop.set()
        for j in list(app.jobs.values()):
            j.cancel.set()
        httpd.server_close()
    return 0
