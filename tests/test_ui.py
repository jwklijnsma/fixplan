"""Browser test of the web UI (Playwright + Chromium). Skipped when Playwright is missing.

Run it locally with:  pip install -e ".[dev,ui]" && playwright install chromium && pytest tests/test_ui.py
"""

from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from fixplan.web import App, make_handler  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample"


@pytest.fixture
def ui(tmp_path, cfg, ollama):
    cfg.ollama_host = ollama.url
    app = App(cfg, "", tmp_path / "jobs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    app.stop.set()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def page():
    with sync_api.sync_playwright() as pw:
        exe = os.environ.get("FIXPLAN_CHROME")   # e.g. /usr/bin/google-chrome
        browser = pw.chromium.launch(executable_path=exe or None)
        pg = browser.new_page(viewport={"width": 1300, "height": 900})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
        yield pg
        browser.close()


def test_upload_and_plan(ui, page, tmp_path):
    page.goto(ui)
    page.wait_for_selector("#go")
    assert "qwen2.5-coder" in page.inner_text("#status")
    page.set_input_files("#fileInput", [str(SAMPLE / "Dockerfile"), str(SAMPLE / "k8s" / "deploy.yaml")])
    assert "2 file(s)" in page.inner_text("#chips")
    page.fill("#paste", (SAMPLE / "app.log").read_text())
    page.fill("#pasteName", "app.log")
    page.fill("#goal", "make it run on OpenShift")
    page.click("#go")
    page.wait_for_selector(".step", timeout=30000)
    assert page.inner_text(".plan h2") == "Make it work"
    assert page.locator(".step").count() >= 2
    assert "kubectl apply" in page.inner_text(".step pre")
    page.click("text=Findings (")
    assert "FP020" in page.inner_text("table")
    page.click("text=Files (")
    assert "app.log" in page.inner_text("table")
    page.screenshot(path=str(tmp_path / "plan.png"), full_page=True)
    # the job list shows it, and it opens again after a reload
    page.reload()
    page.click(".job")
    page.wait_for_selector(".step")
    assert page.errors == []


def test_rules_only_and_validation(ui, page):
    page.goto(ui)
    page.wait_for_selector("#go")
    page.click("#go")
    assert "Add a file" in page.inner_text("#err")
    page.fill("#paste", "FROM python\nUSER root\n")
    page.fill("#pasteName", "Dockerfile")
    page.check("#noLlm")
    page.click("#go")
    page.wait_for_selector(".step", timeout=30000)
    assert "rules only" in page.inner_text(".plan .meta")
    page.once("dialog", lambda d: d.accept())
    page.click("text=Delete")
    page.wait_for_selector("#go")
    assert page.errors == []
