"""Tests for the browser check (webcheck.py and DualMode.browser_check), with a fake Codex: no browser starts,
nothing is billed."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import browser, sessions, webcheck  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import DualMode  # noqa: E402
from test_app import FakeCodex, answer, ev  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(browser, "available", lambda: None)  # as if Playwright were installed


def site(root: Path) -> Path:
    (root / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    (root / "about").mkdir()
    (root / "about" / "team.html").write_text("<p>Team</p>", encoding="utf-8")
    (root / "style.css").write_text("h1 { color: red }", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]", encoding="utf-8")
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "node_modules" / "x" / "index.html").write_text("dep", encoding="utf-8")
    return root


# --- what counts as a web change ----------------------------------------------------------------------

def test_changed_paths():
    diff = "--- a/index.html\n+++ b/index.html\n@@\n--- /dev/null\n+++ b/new.css\n+++ b/index.html\n"
    assert webcheck.changed_paths(diff, "+++ b/app.py\n") == ["index.html", "new.css", "app.py"]


def test_is_web_change(tmp_path):
    assert webcheck.is_web_change(["src/App.tsx"], tmp_path)
    assert webcheck.is_web_change(["styles/main.scss"], tmp_path)
    assert not webcheck.is_web_change(["app.py", "README.md"], tmp_path)
    assert not webcheck.is_web_change(["server.js"], tmp_path)  # a script, and no web pages: probably not
    site(tmp_path)
    assert webcheck.is_web_change(["app.js"], tmp_path)  # a script next to web pages


def test_html_pages_index_first_and_never_hidden_or_dependencies(tmp_path):
    site(tmp_path)
    assert webcheck.html_pages(tmp_path) == ["index.html", "about/team.html"]


# --- the page server ----------------------------------------------------------------------------------

def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_the_page_server_shows_pages_but_never_secrets(tmp_path):
    site(tmp_path)
    with webcheck.StaticServer(tmp_path) as server:
        assert server.url.startswith("http://127.0.0.1:")
        assert fetch(server.url + "index.html") == (200, "<h1>Hello</h1>")
        assert fetch(server.url + "about/team.html")[0] == 200
        assert fetch(server.url + "style.css")[0] == 200
        for path in (".env", ".git/config", "node_modules/x/index.html", "about/", "%2e%2e/", "..%2f..%2fsecret"):
            assert fetch(server.url + path)[0] == 404, path
    with pytest.raises(OSError):
        urllib.request.urlopen(server.url, timeout=2)  # stopped after the check


def test_report():
    text = webcheck.report({"checked": True, "works": False, "pages": ["http://127.0.0.1:1/"],
                            "problems": ["The new button is missing."], "summary": "Opened the home page."})
    assert text == ("Result: PROBLEMS FOUND\nOpened the home page.\nPages opened: http://127.0.0.1:1/\n"
                    "- The new button is missing.")
    assert webcheck.report({"checked": False, "works": False, "summary": "x"}).startswith("Result: COULD NOT CHECK")


# --- in dual mode -------------------------------------------------------------------------------------

CHECK = json.dumps({"checked": True, "works": False, "pages": ["http://127.0.0.1/index.html"],
                    "problems": ["The heading is still black."], "summary": "Opened the home page."})


def worker_turn(diff):
    return [ev("turn/diff/updated", diff=diff)] + answer("Changed the page.")


def test_a_web_change_is_looked_at_before_the_verdict(tmp_path):
    site(tmp_path)
    diff = "--- a/style.css\n+++ b/style.css\n-h1 { color: black }\n+h1 { color: red }\n"
    codex = FakeCodex({1: [answer("1. make it red"), answer(json.dumps({"verdict": "CHANGES_NEEDED", "summary": "s",
                                                                          "fixes": ["fix the colour"]})),
                           answer(json.dumps({"verdict": "APPROVED", "summary": "ok", "fixes": []}))],
                       2: [worker_turn(diff), worker_turn(diff)], 3: [answer(CHECK)], 4: [answer(CHECK)]})
    events = []
    mode = DualMode(codex, sdk, tmp_path, Settings(fresh_review=False, max_review_rounds=1), on_event=lambda a, k, t: events.append((a, k, t)))
    mode.run("make the heading red")

    check = codex.started[2]
    assert check["sandbox"] == sdk.Sandbox.read_only and check["ephemeral"] is True
    server = check["config"]["mcp_servers"]["codex_browser"]
    assert "--headless" in server["args"] and "--confirm-dir" not in server["args"]  # nobody to ask: risky clicks refused
    assert server["args"][server["args"].index("--allow-sites") + 1] == "localhost,127.0.0.1"
    asked = next(t["text"] for t in codex.turns if t["thread"] == "thr_3")
    assert "make the heading red" in asked and "style.css" in asked and "http://127.0.0.1:" in asked
    assert "/index.html" in asked and codex.turns[2]["options"]["output_schema"] == webcheck.CHECK_SCHEMA
    review = next(t["text"] for t in codex.turns if t["thread"] == "thr_1" and "WORKER'S REPORT" in t["text"])
    assert "BROWSER CHECK" in review and "PROBLEMS FOUND" in review and "The heading is still black." in review
    assert len(codex.started) == 4  # checked again after the fix round
    assert ("browser-check", "text", webcheck.report(json.loads(CHECK))) in events


def test_no_check_for_other_changes_or_when_off(tmp_path):
    site(tmp_path)
    for settings, diff in ((Settings(fresh_review=False), "--- a/app.py\n+++ b/app.py\n"),
                           (Settings(browser_check=False, fresh_review=False), "--- a/index.html\n+++ b/index.html\n")):
        codex = FakeCodex({1: [answer("plan"), answer(json.dumps({"verdict": "APPROVED", "summary": "ok",
                                                                   "fixes": []}))],
                           2: [worker_turn(diff)]})
        DualMode(codex, sdk, tmp_path, settings).run("x")
        assert len(codex.started) == 2 and "BROWSER CHECK" not in codex.turns[-1]["text"]


def test_no_playwright_means_no_check_and_one_notice(tmp_path, monkeypatch):
    site(tmp_path)
    monkeypatch.setattr(browser, "available", lambda: "the browser needs Playwright")
    diff = "--- a/index.html\n+++ b/index.html\n"
    notices = []
    codex = FakeCodex({1: [answer("plan"), answer(json.dumps({"verdict": "APPROVED", "summary": "ok", "fixes": []}))],
                       2: [worker_turn(diff)]})
    DualMode(codex, sdk, tmp_path, Settings(fresh_review=False), on_event=lambda a, k, t: k == "notice" and notices.append(t)).run("x")
    assert len(codex.started) == 2 and sum("No browser check" in n for n in notices) == 1


def test_a_dev_server_address_is_used_instead_of_the_folder(tmp_path):
    diff = "--- a/src/App.tsx\n+++ b/src/App.tsx\n"
    codex = FakeCodex({1: [answer("plan"), answer(json.dumps({"verdict": "APPROVED", "summary": "ok", "fixes": []}))],
                       2: [worker_turn(diff)], 3: [answer(CHECK)]})
    DualMode(codex, sdk, tmp_path, Settings(fresh_review=False, check_url="http://localhost:5173")).run("x")
    asked = next(t["text"] for t in codex.turns if t["thread"] == "thr_3")
    assert "dev server runs at http://localhost:5173" in asked and "127.0.0.1:" not in asked


def test_a_failed_check_is_reported_not_fatal(tmp_path):
    site(tmp_path)

    class Broken(FakeCodex):
        def thread_start(self, **options):
            thread = super().thread_start(**options)
            if "mcp_servers" in options.get("config", {}):
                def turn(text, **o):
                    raise sdk.CodexError("browser server didn't start")
                thread.turn = turn
            return thread
    codex = Broken({1: [answer("plan"), answer(json.dumps({"verdict": "APPROVED", "summary": "ok", "fixes": []}))],
                    2: [worker_turn("--- a/index.html\n+++ b/index.html\n")]})
    assert "ok" in DualMode(codex, sdk, tmp_path, Settings(fresh_review=False)).run("x")
    assert "COULD NOT CHECK" in codex.turns[-1]["text"] and "didn't start" in codex.turns[-1]["text"]


# --- /browser check -----------------------------------------------------------------------------------

def test_browser_check_command(tmp_path, capsys):
    import main
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "dual")
    app.command("/browser check off")
    assert app.settings.browser_check is False and "Browser check is off" in capsys.readouterr().out
    app.command("/browser check localhost:3000")
    assert app.settings.check_url == "http://localhost:3000" and app.settings.browser_check is True
    assert "your dev server at http://localhost:3000" in capsys.readouterr().out
    app.command("/browser check https://example.com")
    assert app.settings.check_url == "http://localhost:3000" and "only opens this computer" in capsys.readouterr().out
    app.command("/browser check folder")
    assert app.settings.check_url == "" and "served by the app" in capsys.readouterr().out
