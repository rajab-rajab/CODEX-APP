"""Rules that stop the built-in browser being used to attack websites.

The people misusing a tool are its own users, so "ask the user first" protects nothing here: these rules
apply whatever the user says, and they live in code, not in what Codex is told.

- Attack input: text typed into a page, and addresses opened, are refused when they carry typical attack
  strings (SQL injection, script injection, path traversal, command injection, template or Log4Shell
  strings ...).
- Internal networks: private, link-local and cloud-metadata addresses are refused (routers, company
  intranets, 169.254.169.254). Your own computer (localhost) stays allowed for development, and
  --allow-private turns this off for people who need their own network.
- Rate limits: a cap on actions per minute, on page loads per site per minute and on submits per hour,
  so the browser can't brute-force a login or flood a site.
- CAPTCHAs: a page asking "are you a robot" is never clicked or typed into.
- Allowlist: with --allow-sites, only those sites (and their subdomains) open at all.
- Every action goes into a misuse log (without what was typed), so misuse can be traced."""

import ipaddress
import json
import re
import socket
import time
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import unquote_plus, urlparse

MAX_TYPED = 2000             # characters typed in one go
ACTIONS_PER_MINUTE = 40      # any browser tool
LOADS_PER_SITE_MINUTE = 20   # pages opened on one site
SUBMITS_PER_HOUR = 15        # risky clicks and submits, all sites

# Each rule matches what attack tools and payload lists send, not ordinary text or code: a GitHub comment
# with `code`, JavaScript with ${x} or a sentence like "pets; cat or dog" must still work.
_ATTACKS = [
    (r"'\s*(or|and)\s*'?\d+'?\s*=\s*'?\d+|'\s*(or|and)\s+'[^']*'\s*=\s*'|\bor\s+1\s*=\s*1\b", "SQL injection"),
    (r"\bunion\b[\s/*]+(all[\s/*]+)?select\b|;\s*(drop|truncate|alter)\s+(table|database)\b", "SQL injection"),
    (r"\b(sleep|benchmark|pg_sleep)\s*\(\s*\d|\bwaitfor\s+delay\b|\bxp_cmdshell\b|\binformation_schema\.", "SQL injection"),
    (r"'\s*;?\s*--\s*$|'\s*;\s*--|/\*!\d+|/\*\*/", "SQL injection"),
    (r"<\s*(script|iframe|object|embed)\b|<\s*/\s*script\s*>", "script injection"),
    (r"<[^>]*\bon(error|load|mouseover|focus|toggle|begin|animationstart)\s*=", "script injection"),
    (r"\bjavascript\s*:\s*\S|\bvbscript\s*:|\bdata\s*:\s*text/html", "script injection"),
    (r"(\.\.[/\\]){2,}|(%2e%2e(%2f|%5c|/|\\)){2,}|%252e%252e|\.\.%c0%af", "path traversal"),
    (r"/etc/(passwd|shadow)\b|\bwin\.ini\b|\bboot\.ini\b", "path traversal"),
    (r"(;|\|\||&&|\|)\s*(cat\s+/|ls\s+-\w|whoami\b|wget\s+https?:|curl\s+(-\w+\s+)*https?:|nc\s+-\w|ncat\b|"
     r"bash\s+-[ci]|sh\s+-c\b|powershell(\.exe)?\s+-|cmd(\.exe)?\s+/c|rm\s+-\w*r)", "command injection"),
    (r"\$\(\s*(cat|ls|id|whoami|curl|wget|nc|bash|sh)\b", "command injection"),
    (r"\$\{\s*(jndi|env|sys|java|lower|upper|::-)[:}]", "Log4Shell-style injection"),
    (r"\{\{\s*\d+\s*[*+]\s*\d+\s*\}\}|\$\{\s*\d+\s*[*+]\s*\d+\s*\}|<%=\s*\d+\s*[*+]\s*\d+\s*%>|"
     r"\{\{\s*(config|self|request|cycler|lipsum)\b|__class__\s*\.\s*__|__globals__|__subclasses__", "template injection"),
    (r"<\?php|<\?=|\bbase64_decode\s*\(|\bassert\s*\(\s*\$_|\b(shell_exec|passthru)\s*\(", "code injection"),
    (r"<!entity\s+\S+\s+system\b|<!doctype[^>]*\[\s*<!entity", "XML injection"),
]
_ATTACK_RES = [(re.compile(p, re.IGNORECASE | re.DOTALL), why) for p, why in _ATTACKS]

# A CAPTCHA is recognised from the page's structure (the frames and boxes the CAPTCHA services put on a
# page) and a few unmistakable phrases, not from words like "turnstile" that ordinary pages use too.
CAPTCHA_SCRIPT = """() => !!document.querySelector(
  'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="challenges.cloudflare.com"], ' +
  'iframe[title*="captcha" i], .g-recaptcha, .h-captcha, .cf-turnstile, #cf-challenge-running, ' +
  '#challenge-form, [data-sitekey]')"""
_CAPTCHA_WORDS = re.compile(r"i'?m not a robot|verify (that )?you are (a )?human|are you a robot|"
                            r"complete the (security check|captcha)|checking (if the site connection is secure|your browser)",
                            re.IGNORECASE)


class Blocked(Exception):
    """Refused by a misuse rule. The message says why, for Codex to pass on."""


def attack_in(text: str) -> Optional[str]:
    """The kind of attack string in `text` (also after URL-decoding), or None."""
    if not text:
        return None
    for candidate in {text, unquote_plus(text), unquote_plus(unquote_plus(text))}:
        for pattern, why in _ATTACK_RES:
            if pattern.search(candidate):
                return why
    return None


def captcha_in(page_text: str, has_widget: bool = False) -> bool:
    return has_widget or bool(_CAPTCHA_WORDS.search(page_text or ""))


def _host_matches(host: str, site: str) -> bool:
    host, site = host.lower().rstrip("."), site.lower().strip().lstrip("*.").rstrip(".")
    return host == site or host.endswith("." + site)


def _private(host: str) -> Optional[str]:
    """Why an address is an internal one (not the user's own computer), or None."""
    names = {host}
    try:
        names |= {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        pass
    for name in names:
        try:
            ip = ipaddress.ip_address(name.strip("[]"))
        except ValueError:
            continue
        if ip.is_loopback:
            continue  # the user's own computer: local development stays possible
        if ip in ipaddress.ip_network("169.254.169.254/32") or ip.is_link_local:
            return "a cloud metadata or link-local address"
        if ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "an address on a private network (a router, intranet or other device)"
    return None


class Guard:
    def __init__(self, allow_sites: tuple = (), allow_private: bool = False, log_file: Optional[str] = None,
                 now=time.monotonic):
        self.allow_sites = tuple(s for s in allow_sites if s.strip())
        self.allow_private = allow_private
        self.log_file = Path(log_file) if log_file else None
        self.now = now
        self.actions: deque = deque()
        self.loads: dict = {}
        self.submits: deque = deque()

    # --- the log ------------------------------------------------------------------------------------

    def log(self, action: str, url: str = "", detail: str = "", refused: str = "") -> None:
        if not self.log_file:
            return
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, "site": urlparse(url).hostname or "",
                 "url": url[:300], "detail": detail[:200], "refused": refused}
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def refuse(self, action: str, url: str, why: str):
        self.log(action, url, refused=why)
        raise Blocked(why)

    # --- the rules ----------------------------------------------------------------------------------

    def _window(self, times: deque, seconds: float, limit: int) -> bool:
        cutoff = self.now() - seconds
        while times and times[0] < cutoff:
            times.popleft()
        if len(times) >= limit:
            return False
        times.append(self.now())
        return True

    def action(self, name: str, url: str = "") -> None:
        if not self._window(self.actions, 60, ACTIONS_PER_MINUTE):
            self.refuse(name, url, f"Too many browser actions (over {ACTIONS_PER_MINUTE} a minute). Slow down; "
                                   "the browser is for doing what a person would do, not for automated runs.")

    def check_url(self, url: str) -> None:
        """Before opening `url`."""
        host = urlparse(url).hostname or ""
        why = attack_in(url)
        if why:
            self.refuse("open", url, f"The address carries a {why} string. The browser isn't for attacking websites.")
        if self.allow_sites and not any(_host_matches(host, s) for s in self.allow_sites):
            self.refuse("open", url, f"{host} isn't one of the sites this browser may open "
                                     f"({', '.join(self.allow_sites)}).")
        if not self.allow_private:
            private = _private(host)
            if private:
                self.refuse("open", url, f"{host} is {private}. The browser only opens public websites and this "
                                         "computer (localhost).")
        times = self.loads.setdefault(host, deque())
        if not self._window(times, 60, LOADS_PER_SITE_MINUTE):
            self.refuse("open", url, f"Too many pages opened on {host} (over {LOADS_PER_SITE_MINUTE} a minute).")

    def check_request(self, url: str) -> None:
        """Any request a page makes (images, scripts, frames, redirects): never to an internal network."""
        if self.allow_private:
            return
        host = urlparse(url).hostname or ""
        if not host or urlparse(url).scheme not in ("http", "https", "ws", "wss"):
            return  # data:, blob: and the like stay inside the page
        cache = self.__dict__.setdefault("_private_cache", {})
        if host not in cache:
            cache[host] = _private(host)
        if cache[host]:
            raise Blocked(f"{host} is {cache[host]}.")

    def check_navigation(self, url: str) -> None:
        """A page the browser goes to (opened, clicked, redirected): allowed sites, no internal networks."""
        host = urlparse(url).hostname or ""
        if self.allow_sites and host and not any(_host_matches(host, s) for s in self.allow_sites):
            raise Blocked(f"{host} isn't one of the sites this browser may open ({', '.join(self.allow_sites)}).")
        self.check_request(url)

    def check_typed(self, text: str, url: str) -> None:
        if len(text) > MAX_TYPED:
            self.refuse("type", url, f"That's more than {MAX_TYPED} characters to type in one go.")
        why = attack_in(text)
        if why:
            self.refuse("type", url, f"That text is a {why} string. The browser isn't for attacking websites or "
                                     "testing their security.")

    def check_page(self, page_text: str, url: str, action: str, has_widget: bool = False) -> None:
        if captcha_in(page_text, has_widget):
            self.refuse(action, url, "The page is asking whether you are a human (a CAPTCHA). The browser never "
                                     "gets past those: the user can do it in the window themselves.")

    def check_submit(self, url: str) -> None:
        if not self._window(self.submits, 3600, SUBMITS_PER_HOUR):
            self.refuse("submit", url, f"Too many submits (over {SUBMITS_PER_HOUR} an hour). That looks like "
                                       "automated use, which the browser doesn't do.")
