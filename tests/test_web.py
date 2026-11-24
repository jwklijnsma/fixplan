import base64
from http.server import ThreadingHTTPServer
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile

import pytest

from fixplan.web import App, make_handler, serve

from conftest import DEPLOY, DOCKERFILE, LOG

TOKEN = "t0ken-for-tests"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


@pytest.fixture
def server(tmp_path, cfg, ollama):
    cfg.ollama_host = ollama.url
    app = App(cfg, TOKEN, tmp_path / "jobs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url, app
    app.stop.set()
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, token=TOKEN, headers=None, csrf=True, cookie=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if csrf and method != "GET":
        h["X-Fixplan"] = "1"
    if cookie:
        h["Cookie"] = cookie
    elif token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(url, jid):
    for _ in range(200):
        _, j = call(f"{url}/api/jobs/{jid}")
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def b64(s) -> str:
    return base64.b64encode(s.encode() if isinstance(s, str) else s).decode()


def test_status(server):
    url, _ = server
    code, st = call(url + "/api/status")
    assert code == 200 and st["ollama"] and st["model_available"] and st["auth"]
    assert st["models"] == ["qwen2.5-coder:14b"] and "shellcheck" in st["linters"]


def test_upload_files_and_paste(server):
    url, app = server
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo/k8s/deploy.yaml", DEPLOY)
        z.writestr("repo/Dockerfile", DOCKERFILE)
    code, job = call(url + "/api/jobs", "POST", {
        "files": [{"name": "repo.zip", "data": b64(buf.getvalue())}],
        "text": LOG, "text_name": "app.log", "goal": "run on OpenShift", "language": "en"})
    assert code == 202 and job["status"] in ("queued", "running")
    j = wait(url, job["id"])
    assert j["status"] == "done", j
    assert j["plan"]["steps"] and j["goal"] == "run on OpenShift" and j["counts"]["high"] >= 1
    assert {f["path"] for f in j["plan"]["files"]} == {"k8s/deploy.yaml", "Dockerfile", "app.log"}
    assert any("received 3 file(s)" in ln for ln in j["lines"]) and any("✓" in ln for ln in j["lines"])
    code, f = call(f"{url}/api/jobs/{job['id']}/file?path=k8s/deploy.yaml")
    assert code == 200 and "containerPort" in f["content"]
    assert call(f"{url}/api/jobs/{job['id']}/file?path=../../etc/passwd")[0] == 404
    code, md = call(f"{url}/api/jobs/{job['id']}/plan.md")
    assert code == 200 and "## Plan" in md
    code, jobs = call(url + "/api/jobs")
    assert [x["id"] for x in jobs["jobs"]] == [job["id"]]
    # kept on disk and loaded again by a new App (snippets too)
    app2 = App(app.cfg, TOKEN, app.data_dir)
    j2 = app2.jobs[job["id"]]
    assert j2.plan["title"] == j["plan"]["title"] and "FROM" in app2.file_text(j2, "Dockerfile")
    app2.stop.set()
    assert call(f"{url}/api/jobs/{job['id']}/delete", "POST")[0] == 200
    assert not (app.data_dir / job["id"]).exists()


def test_rules_only(server):
    url, _ = server
    _, job = call(url + "/api/jobs", "POST", {"text": DOCKERFILE, "text_name": "Dockerfile", "no_llm": True})
    j = wait(url, job["id"])
    assert j["plan"]["generator"] == "rules" and j["rules_only"]


def test_model_setting(server):
    url, app = server
    code, r = call(url + "/api/settings", "POST", {"model": "llama3.1:8b"})
    assert code == 200 and r["model_source"] == "ui" and app.model == "llama3.1:8b"
    _, st = call(url + "/api/status")
    assert st["model"] == "llama3.1:8b" and st["model_available"] is False
    assert call(url + "/api/settings", "POST", {"model": "bad name;rm"})[0] == 400
    call(url + "/api/settings", "POST", {"model": ""})
    assert app.model == app.default_model


def test_cancel_queued(server):
    url, app = server
    app.stop.set()            # stop the worker so the job stays queued
    time.sleep(0.6)
    _, job = call(url + "/api/jobs", "POST", {"text": DOCKERFILE, "text_name": "Dockerfile"})
    assert job["position"] == 1
    code, j = call(f"{url}/api/jobs/{job['id']}/cancel", "POST")
    assert code == 200 and j["status"] == "cancelled"
    assert call(f"{url}/api/jobs/{job['id']}/cancel", "POST")[0] == 409


def test_bad_requests(server):
    url, _ = server
    assert call(url + "/api/jobs", "POST", {})[0] == 400
    assert call(url + "/api/jobs", "POST", {"files": [{"name": "x", "data": "!!notbase64"}]})[0] == 400
    assert call(url + "/api/jobs", "POST", {"text": "x"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(url + "/api/jobs/0123456789abcdef")[0] == 404
    assert call(url + "/nope")[0] == 404


def test_auth(server):
    url, _ = server
    assert call(url + "/api/jobs", token=None)[0] == 401
    assert call(url + "/api/jobs", token="wrong")[0] == 401
    assert call(url + "/healthz", token=None)[0] == 200
    # cookie flow: ?token= sets an HttpOnly cookie and redirects
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(f"{url}/?token={TOKEN}")
        raise AssertionError("expected a redirect")
    except urllib.error.HTTPError as e:
        assert e.code == 302 and "HttpOnly" in e.headers["Set-Cookie"] and "SameSite=Strict" in e.headers["Set-Cookie"]
        cookie = e.headers["Set-Cookie"].split(";")[0]
    assert call(url + "/api/jobs", token=None, cookie=cookie)[0] == 200
    # a browser POST with only the cookie needs the CSRF header
    assert call(url + "/api/settings", "POST", {"model": ""}, token=None, cookie=cookie, csrf=False)[0] == 403
    assert call(url + "/api/settings", "POST", {"model": ""}, token=None, cookie=cookie)[0] == 200
    # login endpoint
    assert call(url + "/api/login", "POST", {"token": "nope"}, token=None)[0] == 401
    assert call(url + "/api/login", "POST", {"token": TOKEN}, token=None)[0] == 200


def test_static_files(server):
    url, _ = server
    with urllib.request.urlopen(url + "/") as r:
        body = r.read().decode()
        assert '<div id="root">' in body and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    with urllib.request.urlopen(url + "/app.js") as r:
        assert r.headers["Content-Type"].startswith("text/javascript")
    assert call(url + "/../../etc/passwd")[0] == 404


def test_placeholder_token_refused(cfg, tmp_path, capsys):
    assert serve(cfg, port=0, token="changeme", data_dir=str(tmp_path)) == 2
    assert "example value" in capsys.readouterr().out
