"""Giving you back files Codex's Windows sandbox made unreadable.

On Windows, Codex runs commands as a separate sandbox account (CodexSandboxUsers). When it edits a file with
its own edit tool, the file keeps the folder's permissions. But when a command it runs writes a new file, or
replaces one (PowerShell Set-Content or [IO.File]::WriteAllText, Python, pytest's cache folders ...), the
new file can belong to the sandbox account alone. Then you can't open it, git can't read it (so /undo and
the list of changed files stop working), and team mode can't merge it. Found in a real run: a rewritten
ledger/report.py and a new test file that the user's own account couldn't read.

So after every turn, Codex App looks for such files in the project (and in team workers' worktrees before
they are merged). The sandbox account owns them, and an owner may change a file's permissions, so the app
asks Codex's own sandbox to grant your account full access again (icacls /grant), checks that it worked,
and tells you. Nothing changes on other systems, where the sandbox runs as you. CODEX_APP_FIX_PERMISSIONS=no
turns it off."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

SKIP = {"node_modules", ".venv", "venv", "env", ".tox", ".mypy_cache", "dist", "build", ".next"}
SCAN_LIMIT = 20_000      # files and folders looked at per check
CHUNK = 40               # paths per sandbox call (the command line has a length limit)

# Runs inside Codex's sandbox, as the account that owns the files: give the user full access back.
_GRANT = r"""
import os, subprocess, sys
user = sys.argv[1]
for p in sys.argv[2:]:
    rights = user + (":(OI)(CI)F" if os.path.isdir(p) else ":F")
    done = subprocess.run(["icacls", p, "/grant", rights, "/T", "/C", "/Q"], capture_output=True, text=True)
    print(("ok " if done.returncode == 0 else "failed ") + p, flush=True)
"""


def applies() -> bool:
    return os.name == "nt"


def _opens(path: Path) -> bool:
    try:
        with open(path, "rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        return True  # gone, locked by another program ...: not a permission problem


def find_unreadable(root: Path, limit: int = SCAN_LIMIT) -> list:
    """Files and folders in `root` that this account can't open, relative to root ([] off Windows). A folder
    that can't be listed is reported itself, not what is inside it."""
    if not applies():
        return []
    root, found, seen = Path(root), [], 0
    stack = [root]
    while stack and seen < limit:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except PermissionError:
            found.append(folder.relative_to(root).as_posix())
            continue
        except OSError:
            continue
        for entry in entries:
            seen += 1
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and not _opens(Path(entry.path)):
                    found.append(Path(entry.path).relative_to(root).as_posix())
            except PermissionError:
                found.append(Path(entry.path).relative_to(root).as_posix())
            except OSError:
                continue
    return sorted(found)


def codex_exe() -> Optional[str]:
    try:
        from codex_cli_bin import bundled_codex_path
        return str(bundled_codex_path())
    except Exception:
        return None


def current_user() -> str:
    domain, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{name}" if domain and name else name


def repair(root: Path, paths: list, run: Callable = subprocess.run) -> tuple:
    """Have Codex's sandbox give this account access to `paths` (relative to root) again.
    Returns (fixed, still unreadable)."""
    exe, user = codex_exe(), current_user()
    if not paths or not exe or not user:
        return [], list(paths)
    for start in range(0, len(paths), CHUNK):
        chunk = paths[start:start + CHUNK]
        try:
            run([exe, "sandbox", "-c", 'sandbox_mode="workspace-write"', "--", sys.executable, "-c", _GRANT, user,
                 *[p.replace("/", "\\") for p in chunk]],
                cwd=str(root), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
    still = set(find_unreadable(root))
    left = [p for p in paths if p in still or any(p.startswith(s + "/") for s in still)]
    return [p for p in paths if p not in left], left


def check_and_repair(root: Path, run: Callable = subprocess.run) -> tuple:
    """Find files this account can't open in `root` and give them back. (fixed, still unreadable)."""
    found = find_unreadable(root)
    return repair(root, found, run) if found else ([], [])


def manual_fix(root: Path, paths: list) -> str:
    """What to run (as administrator) when the automatic repair didn't work."""
    first = Path(root) / paths[0]
    return f'takeown /f "{first}" /r /d y  and then  icacls "{first}" /reset /t'
