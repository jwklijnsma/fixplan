"""Browser test of the React web UI (Playwright + Chromium). Skipped when Playwright is missing.

    pip install -e ".[dev,ui]" && playwright install chromium && pytest tests/test_ui.py

Screenshots go to $FIXPLAN_SHOTS (default: the test's tmp dir), so CI can keep them.
"""

from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from fixplan.web import STATIC_DIR, App, make_handler  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample"
TOKEN = "ui-test-token"

pytestmark = pytest.mark.skipif(not (STATIC_DIR / "app.js").is_file(), reason="web UI not built (make web)")


@pytest.fixture
def ui(tmp_path, cfg, ollama):
    cfg.ollama_host = ollama.url
    app = App(cfg, TOKEN, tmp_path / "jobs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    app.stop.set()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def page(tmp_path):
    shots = Path(os.environ.get("FIXPLAN_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("FIXPLAN_CHROME") or None)
        ctx = browser.new_context(viewport={"width": 1360, "height": 900})
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and "401" not in m.text else None)
        pg.shot = lambda name: pg.screenshot(path=str(shots / f"{name}.png"), full_page=True)
        yield pg
        browser.close()


def test_login(ui, page):
    page.goto(ui)
    page.wait_for_selector("text=Access token needed")
    page.fill("input[type=password]", "wrong")
    page.click("text=Sign in")
    page.wait_for_selector("text=Wrong token.")
    page.fill("input[type=password]", TOKEN)
    page.click("text=Sign in")
    page.wait_for_selector("text=What should be fixed?")
    assert page.errors == []


def test_upload_plan_and_browse(ui, page):
    page.goto(f"{ui}/?token={TOKEN}")                   # token link -> cookie -> clean URL
    page.wait_for_selector("text=What should be fixed?")
    assert "token" not in page.url
    assert "Ollama 0.12.0-mock" in page.inner_text(".topbar")
    assert "linters" in page.inner_text(".topbar")
    page.shot("1-new-plan-empty")

    page.locator("input[type=file]:not([webkitdirectory])").set_input_files(
        [str(SAMPLE / "Dockerfile"), str(SAMPLE / "k8s" / "deploy.yaml")])
    page.wait_for_selector(".picked")
    assert "2 files" in page.inner_text(".picked-head")
    page.fill("textarea.paste", (SAMPLE / "app.log").read_text())
    page.fill(".field.inline input", "app.log")
    page.click("text=Make it run on OpenShift")
    assert page.input_value("input.goal") == "Make it run on OpenShift"
    page.shot("2-new-plan-filled")
    page.click("text=Make the plan")

    page.wait_for_selector(".step", timeout=30000)
    assert page.inner_text(".repo-head h1") == "Make it work"
    assert "Make it run on OpenShift" in page.inner_text(".goal-line")
    assert page.locator(".step").count() >= 2
    assert "kubectl apply" in page.inner_text(".step .codeblock")
    assert page.locator(".step .diff-body .add").count() == 1
    page.shot("3-plan")

    # tick a step off; it is remembered after a reload
    page.locator(".done-check input").first.check()
    assert "1/" in page.inner_text(".done-count")
    page.reload()
    page.wait_for_selector(".step")
    assert "1/" in page.inner_text(".done-count")

    # findings: filter, expand, code snippet from the upload
    page.click("role=tab[name=/Findings/]")
    page.wait_for_selector(".file-group")
    page.click("text=Privileged container.")
    page.wait_for_selector(".f-detail .code tr.hl")
    assert "privileged: true" in page.inner_text(".f-detail .code tr.hl")
    page.click(".seg >> text=Model")
    groups = page.inner_text("main")
    assert "port-mismatch" in groups and "FP020" not in groups
    page.shot("4-findings")

    page.click("role=tab[name=/Files/]")
    page.shot("5-files")
    page.click(".files-table >> text=deploy.yaml")
    page.wait_for_selector(".files-table .code")
    assert "apiVersion" in page.inner_text(".files-table .code")

    page.click("role=tab[name=/Report/]")
    page.wait_for_selector(".md h2")
    assert "Plan" in page.inner_text(".md")

    page.click("role=tab[name=/Log/]")
    assert "received 3 file(s)" in page.inner_text(".log")

    # jobs panel
    page.click(".jobs-pill")
    page.wait_for_selector(".jobs-panel")
    assert "Make it work" in page.inner_text(".jobs-panel")
    page.keyboard.press("Escape")

    # model picker
    page.click(".model-picker .pill-button")
    page.fill(".popover input", "llama3.1:8b")
    assert "Not in Ollama yet" in page.inner_text(".popover")
    page.click("text=Use this model")
    page.wait_for_selector("text=New plans use llama3.1:8b.")

    # sidebar lists it; delete it
    assert "Make it work" in page.inner_text(".sidebar")
    page.once("dialog", lambda d: d.accept())
    page.click(".actions >> text=Delete")
    page.wait_for_selector("text=What should be fixed?")
    assert "No plans yet" in page.inner_text(".sidebar")
    assert page.errors == []


def test_rules_only_and_validation(ui, page):
    page.goto(f"{ui}/?token={TOKEN}")
    page.wait_for_selector("text=What should be fixed?")
    assert page.is_disabled("text=Make the plan")
    page.fill("textarea.paste", "FROM python\nUSER root\n")
    page.fill(".field.inline input", "Dockerfile")
    page.check("text=Rules only (no model)")
    page.click("text=Make the plan")
    page.wait_for_selector(".step", timeout=30000)
    assert "rules only" in page.inner_text(".repo-sub")
    assert page.locator(".sidebar .zip-badge").inner_text() == "RULES"
    assert page.errors == []
