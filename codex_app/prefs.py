"""Saved preferences per project (/save), and the signal when a long task finishes.

/save remembers this project's mode, power, profile, permission, approval, models, efforts, team size,
web search, finish signal and memory on/off in ~/.codex-app/projects.json (set CODEX_APP_HOME to move it). They are used
the next time the app starts on that project. Command-line options still win over them, and saved
preferences win over environment variables."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import config

KEYS = ("mode", "power", "profile", "permission", "approval", "single_model", "single_effort",
        "planner_model", "planner_effort", "worker_model", "worker_effort", "team_size", "web_search", "notify",
        "browser", "memory", "browser_check", "check_url")


def _file() -> Path:
    return config.APP_HOME / "projects.json"


def _key(root) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _load_all() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load(root) -> dict:
    saved = _load_all().get(_key(root), {})
    return {k: v for k, v in saved.items() if k in KEYS} if isinstance(saved, dict) else {}


def save(root, values: dict) -> dict:
    data = _load_all()
    data[_key(root)] = {k: values[k] for k in KEYS if k in values}
    _file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_file())  # write then rename, so a crash never leaves half a file
    return data[_key(root)]


def clear(root) -> bool:
    data = _load_all()
    if _key(root) not in data:
        return False
    del data[_key(root)]
    _file().write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return True


def apply(settings, saved: dict) -> None:
    """Put saved preferences into settings (the mode is the caller's: it isn't a setting)."""
    for key, value in saved.items():
        if key != "mode" and hasattr(settings, key):
            setattr(settings, key, value)


# --- the finish signal ---------------------------------------------------------------------------

_TOAST = """\
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(10000, 'Codex App', '{text}', 'Info')
Start-Sleep -Seconds 11
$n.Dispose()
"""


def signal_done(text: str, popen=subprocess.Popen) -> Optional[object]:
    """A beep, and on Windows a notification in the corner of the screen, so you know a long task ended
    while you were doing something else. Never waits, never fails."""
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    if os.name != "nt":
        return None
    script = _TOAST.replace("{text}", text.replace("'", "''")[:200])
    try:
        return popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None
