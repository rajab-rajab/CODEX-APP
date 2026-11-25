"""The browser check: in dual and team mode, a change to a web page is looked at in a real browser before the
planner gives its verdict, like the official Codex app's in-app browser verification.

When the worker's changes touch web files (HTML, CSS, JSX/TSX, Vue, Svelte; JavaScript or TypeScript when the
project has HTML pages), a separate, read-only checker opens the pages in Codex App's own browser: headless,
only this computer (localhost), and no clicks that need a yes (there is no one to ask). It reports what it
saw (does the page load, does the change show and work, errors) and the planner weighs that like a test
result. The checker never changes files.

The pages come from a small web server the app runs for the check: the project folder on 127.0.0.1 with a
random port, read-only, without secret files (see protect.py) or hidden folders such as .git. Projects that
need their own dev server (React, Vue, Next ...) set CODEX_APP_CHECK_URL, or /browser check <url>, to the
address it runs on; start it yourself first. CODEX_APP_BROWSER_CHECK=no, or /browser check off, turns the
check off."""

import functools
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import protect

PAGE_EXT = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}
SCRIPT_EXT = {".js", ".mjs", ".ts"}  # web files only when the project has HTML pages
_SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".codex-app"}
PAGES_LISTED = 20

CHECK_INSTRUCTIONS = """\
You are the BROWSER CHECKER of a coding team. A worker just changed web files of the user's project. Your job:
look at the result in a real browser and report what you see, so the planner can decide. You never change
files, and your sandbox is read-only.
{rules}
- Use the codex_browser tools: open the page, read it, take a screenshot when the look matters, and try the
  changed parts (links, buttons, forms) when that's safe. Only open the addresses you are given (this
  computer). Never sign in, pay or submit anything real; the browser refuses clicks that would need a yes.
- Check: does the page load, does the change the task asked for show up and work, is anything broken
  (missing content, layout clearly wrong, errors shown on the page)?
- Report only what you saw. If you couldn't check something (for example the page needs a dev server that
  isn't running), say so instead of guessing."""

CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "checked": {"type": "boolean", "description": "False if the pages couldn't be opened or checked."},
        "works": {"type": "boolean", "description": "True if the change shows up and works as the task asks."},
        "pages": {"type": "array", "items": {"type": "string"}, "description": "The addresses you opened."},
        "problems": {"type": "array", "items": {"type": "string"},
                     "description": "What is broken or missing, concretely; empty if nothing."},
        "summary": {"type": "string", "description": "What you checked and saw, briefly."},
    },
    "required": ["checked", "works", "pages", "problems", "summary"],
    "additionalProperties": False,
}


def changed_paths(*texts: str) -> list:
    """The files named in diffs ("+++ b/path" and "--- a/path" lines), in order, without duplicates."""
    found = []
    for text in texts:
        found += re.findall(r"^(?:\+\+\+ b|--- a)/(.+?)\s*$", text or "", re.MULTILINE)
    return list(dict.fromkeys(p for p in found if p != "/dev/null"))


def html_pages(root: Path, limit: int = PAGES_LISTED) -> list:
    """The project's HTML pages, relative to root (index pages first)."""
    root = Path(root)
    pages = []
    for path in root.rglob("*.htm*"):
        rel = path.relative_to(root)
        if path.suffix.lower() not in (".html", ".htm") or any(p in _SKIP_DIRS or p.startswith(".") for p in rel.parts):
            continue
        if protect.is_secret_path(rel.as_posix()):
            continue
        pages.append(rel.as_posix())
        if len(pages) >= limit * 5:
            break
    pages.sort(key=lambda p: (Path(p).stem.lower() != "index", p.count("/"), p))
    return pages[:limit]


def is_web_change(paths: list, root: Path) -> bool:
    """True if the changes touch what a browser shows."""
    exts = {Path(p).suffix.lower() for p in paths}
    if exts & PAGE_EXT:
        return True
    return bool(exts & SCRIPT_EXT) and bool(html_pages(root, limit=1))


class _Handler(SimpleHTTPRequestHandler):
    """Files from the project folder only, read-only, never secret files or hidden folders."""

    def _refused(self) -> bool:
        path = unquote(urlparse(self.path).path).lstrip("/")
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        return any(p.startswith(".") or p in _SKIP_DIRS for p in parts) or protect.is_secret_path(path)

    def do_GET(self):
        if self._refused():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        if self._refused():
            self.send_error(404)
            return
        super().do_HEAD()

    def list_directory(self, path):
        self.send_error(404)  # no folder listings: only the files a page asks for

    def log_message(self, *args):
        pass


class StaticServer:
    """The project folder on http://127.0.0.1:<random port>/ while the check runs."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.server = None

    def __enter__(self) -> "StaticServer":
        handler = functools.partial(_Handler, directory=str(self.root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def __exit__(self, *exc):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


def task(task_text: str, paths: list, base_url: str, pages: list, own_server: bool) -> str:
    """What the checker is asked to do."""
    where = (f"The project is served at {base_url} (a server the app runs for this check: the project's files "
             "as they are now)." if own_server else
             f"The project's own dev server runs at {base_url} (the user started it).")
    listed = "\n".join(f"  {base_url.rstrip('/')}/{p}" for p in pages) if own_server and pages else ""
    return (f"THE TASK THE WORKER DID:\n{task_text}\n\nFILES IT CHANGED:\n" + "\n".join(f"  {p}" for p in paths[:30])
            + f"\n\n{where}" + (f"\nIts HTML pages:\n{listed}" if listed else "")
            + "\n\nOpen the pages that show the change, check it, and give your report in the JSON form your output "
              "schema asks for.")


def report(data: dict) -> str:
    """The checker's findings as text for the planner."""
    if not data.get("checked"):
        state = "COULD NOT CHECK"
    else:
        state = "WORKS" if data.get("works") else "PROBLEMS FOUND"
    lines = [f"Result: {state}", str(data.get("summary", "")).strip()]
    pages = [str(p) for p in data.get("pages") or []]
    if pages:
        lines.append("Pages opened: " + ", ".join(pages[:10]))
    lines += [f"- {p}" for p in (str(x).strip() for x in data.get("problems") or []) if p]
    return "\n".join(line for line in lines if line)
