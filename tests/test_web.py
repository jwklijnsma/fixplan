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


@pytest.fixture
def server(tmp_path, cfg, ollama, request):
    cfg.ollama_host = ollama.url
    token = getattr(request, "param", "")
    app = App(cfg, token, tmp_path / "jobs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url, app, token
    app.stop.set()
    httpd.shutdown()
    httpd.server_close()


def call(url, method="GET", body=None, token="", headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(url, jid, token=""):
    for _ in range(100):
        code, j = call(f"{url}/api/jobs/{jid}", token=token)
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def b64(s: str | bytes) -> str:
    return base64.b64encode(s.encode() if isinstance(s, str) else s).decode()


def test_index_and_info(server):
    url, _, _ = server
    with urllib.request.urlopen(url + "/") as r:
        assert b"fixplan" in r.read()
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    code, info = call(url + "/api/info")
    assert code == 200 and info["ollama_ok"] and info["model_ok"]


def test_upload_files_and_paste(server):
    url, app, _ = server
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo/k8s/deploy.yaml", DEPLOY)
        z.writestr("repo/Dockerfile", DOCKERFILE)
    code, job = call(url + "/api/jobs", "POST", {
        "files": [{"name": "repo.zip", "data": b64(buf.getvalue())}],
        "text": LOG, "text_name": "app.log", "goal": "run on OpenShift", "language": "en"})
    assert code == 202
    j = wait(url, job["id"])
    assert j["state"] == "done", j
    assert j["plan"]["steps"] and j["goal"] == "run on OpenShift"
    assert {f["path"] for f in j["plan"]["files"]} == {"k8s/deploy.yaml", "Dockerfile", "app.log"}
    code, md = call(f"{url}/api/jobs/{job['id']}/plan.md")
    assert code == 200 and "## Plan" in md
    code, jobs = call(url + "/api/jobs")
    assert [x["id"] for x in jobs] == [job["id"]]
    # kept on disk, and loaded again by a new App
    app2 = App(app.cfg, "", app.data_dir)
    assert app2.jobs[job["id"]].plan["title"] == j["plan"]["title"]
    app2.stop.set()
    code, _ = call(f"{url}/api/jobs/{job['id']}", "DELETE")
    assert code == 200 and not (app.data_dir / job["id"]).exists()


def test_rules_only_upload(server):
    url, _, _ = server
    code, job = call(url + "/api/jobs", "POST", {"text": DOCKERFILE, "text_name": "Dockerfile",
                                                 "no_llm": True})
    j = wait(url, job["id"])
    assert j["plan"]["generator"] == "rules"


def test_bad_uploads(server):
    url, _, _ = server
    assert call(url + "/api/jobs", "POST", {})[0] == 400
    assert call(url + "/api/jobs", "POST", {"files": [{"name": "x", "data": "!!notbase64"}]})[0] == 400
    assert call(url + "/api/jobs", "POST", {"text": "x"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(url + "/api/jobs/0123456789abcdef")[0] == 404
    assert call(url + "/api/jobs/../../etc/passwd")[0] == 404


def test_cross_site_requests_refused_without_token(server):
    url, _, _ = server
    code, _ = call(url + "/api/jobs", "POST", {"text": "x"}, headers={"Origin": "https://evil.example"})
    assert code == 401
    code, _ = call(url + "/api/jobs", headers={"Origin": url})
    assert code == 200


@pytest.mark.parametrize("server", ["s3cret-token"], indirect=True)
def test_token_required(server):
    url, _, token = server
    assert call(url + "/api/jobs")[0] == 401
    assert call(url + "/api/jobs", token="wrong")[0] == 401
    assert call(url + "/api/jobs", token=token)[0] == 200
    assert call(url + "/healthz")[0] == 200


def test_refuses_public_bind_without_token(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FIXPLAN_WEB_TOKEN", raising=False)
    assert serve(cfg, bind="0.0.0.0", port=0, data_dir=str(tmp_path)) == 2
    assert "without a token" in capsys.readouterr().out
