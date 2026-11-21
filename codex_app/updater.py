"""Keeping Codex App up to date.

The app folder is a git clone of the app's GitHub repository. Updating means downloading the new
commits from GitHub (git fetch) and moving to them (git pull --fast-forward-only). If requirements.txt
changed, the packages are installed again.

Your settings (environment variables) and saved conversations (~/.codex-app) are outside the app
folder, so an update never touches them. An update is refused while you have uncommitted changes to
the app's own files, so nothing you edited is overwritten."""

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import __version__

APP_DIR = Path(__file__).resolve().parents[1]
CHECK_EVERY = 24 * 3600  # seconds between automatic checks
_VERSION = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


@dataclass
class UpdateStatus:
    ok: bool                       # False if the check itself could not be done
    message: str = ""
    current: str = __version__
    latest: str = ""
    behind: int = 0                # new commits on GitHub that this copy doesn't have
    ahead: int = 0                 # local commits that aren't on GitHub
    notes: list = field(default_factory=list)  # commit subjects of the new commits

    @property
    def available(self) -> bool:
        return self.ok and self.behind > 0


def _git(app_dir: Path, *args, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=app_dir, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _version_in(text: str) -> str:
    match = _VERSION.search(text or "")
    return match.group(1) if match else ""


def local_version(app_dir: Path = APP_DIR) -> str:
    try:
        return _version_in((app_dir / "codex_app" / "__init__.py").read_text(encoding="utf-8")) or __version__
    except OSError:
        return __version__


def check(app_dir: Path = APP_DIR, fetch: bool = True, timeout: int = 30) -> UpdateStatus:
    """Ask GitHub whether a newer version exists. Never changes any file."""
    current = local_version(app_dir)
    if not (app_dir / ".git").exists():
        return UpdateStatus(False, "This copy of the app isn't a git clone, so it can't update itself. "
                                   "Install it with 'git clone' (see README.md).", current)
    try:
        upstream = _git(app_dir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=timeout)
        if upstream.returncode != 0:
            return UpdateStatus(False, "This copy has no GitHub branch to update from. "
                                       "Run: git push -u origin main", current)
        if fetch:
            fetched = _git(app_dir, "fetch", "--quiet", timeout=timeout)
            if fetched.returncode != 0:
                return UpdateStatus(False, "Could not reach GitHub to check for updates: "
                                           + (fetched.stderr.strip().splitlines() or ["unknown error"])[-1], current)
        counts = _git(app_dir, "rev-list", "--left-right", "--count", "HEAD...@{u}", timeout=timeout)
        ahead, behind = (int(n) for n in counts.stdout.split())
        notes = _git(app_dir, "log", "--format=%s", "-n", "20", "HEAD..@{u}", timeout=timeout).stdout.splitlines()
        latest = _version_in(_git(app_dir, "show", "@{u}:codex_app/__init__.py", timeout=timeout).stdout) or current
    except FileNotFoundError:
        return UpdateStatus(False, "Git is not installed, so the app can't update itself.", current)
    except subprocess.TimeoutExpired:
        return UpdateStatus(False, "Checking for updates took too long; try again later.", current)
    except ValueError:
        return UpdateStatus(False, "Could not read the update information from git.", current)

    if behind == 0:
        message = f"Codex App {current} is up to date."
    else:
        message = f"Update available: {current} -> {latest} ({behind} new change{'s' if behind > 1 else ''})."
    return UpdateStatus(True, message, current, latest, behind, ahead, notes)


def apply(app_dir: Path = APP_DIR, install_packages: bool = True) -> tuple[bool, str]:
    """Download and install the update. Returns (updated, message for the user)."""
    status = check(app_dir)
    if not status.ok:
        return False, status.message
    if not status.available:
        return False, status.message

    # Each line is "XY path"; the status letters can start with a space, so don't strip the output.
    changed = [line for line in _git(app_dir, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
               if line.strip()]
    if changed:
        files = ", ".join(line[3:] for line in changed[:5])
        return False, (f"Not updated: these app files have changes that aren't committed: {files}. "
                       "Commit them (or undo them with 'git restore <file>') and try again.")
    if status.ahead:
        return False, (f"Not updated: this copy has {status.ahead} commit(s) that aren't on GitHub. "
                       "Push them first (git push), then update.")

    old_requirements = _git(app_dir, "rev-parse", "HEAD:requirements.txt").stdout.strip()
    pulled = _git(app_dir, "pull", "--ff-only", "--quiet", timeout=120)
    if pulled.returncode != 0:
        return False, "The update failed: " + (pulled.stderr.strip() or pulled.stdout.strip() or "unknown git error")

    lines = [f"Updated Codex App {status.current} -> {local_version(app_dir)}."]
    lines += [f"  - {note}" for note in status.notes]

    new_requirements = _git(app_dir, "rev-parse", "HEAD:requirements.txt").stdout.strip()
    if install_packages and new_requirements != old_requirements:
        installed = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                                    str(app_dir / "requirements.txt")], capture_output=True, text=True)
        if installed.returncode == 0:
            lines.append("The required packages were updated too.")
        else:
            lines.append("The packages could not be updated. Run: py -m pip install -r requirements.txt")
    lines.append("Restart the app to use the new version.")
    return True, "\n".join(lines)


def due_notice(app_dir: Path, home: Path, now: Optional[float] = None) -> Optional[str]:
    """A short 'update available' line, checked at most once a day. Never raises, never blocks for long."""
    now = time.time() if now is None else now
    stamp = home / "update-check.json"
    try:
        last = json.loads(stamp.read_text(encoding="utf-8")).get("checked", 0)
    except (OSError, ValueError, AttributeError):
        last = 0
    if now - last < CHECK_EVERY:
        return None
    try:
        status = check(app_dir, timeout=8)
        home.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"checked": now}), encoding="utf-8")
    except Exception:
        return None
    return f"{status.message} Type /update to install it." if status.available else None
