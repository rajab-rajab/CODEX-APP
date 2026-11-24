"""Saving and resuming conversations.

Each conversation is a JSON file in ~/.codex-app/sessions/ (change with CODEX_APP_HOME). A file holds
the project path, a title (the start of your first message), the mode, and for each agent its Codex
thread ID, model and token counts. The conversation itself is kept by Codex. Files never contain
your API key."""

import json
import time
from pathlib import Path
from typing import Optional

from .config import APP_HOME


def sessions_dir() -> Path:
    return APP_HOME / "sessions"


def new_session_path(mode: str) -> Path:
    return sessions_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}.json"


def save(path: Path, project: Path, mode_state: dict, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"project": str(project), "title": title, "saved": time.strftime("%Y-%m-%d %H:%M"), **mode_state}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # write then rename, so a crash never leaves half a file


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_for_project(project: Path, limit: int = 10) -> list[tuple[Path, dict]]:
    """The most recent saved conversations for this project, newest first."""
    folder = sessions_dir()
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = load(path)
        except (OSError, ValueError):
            continue
        if Path(data.get("project", "")) == project:
            found.append((path, data))
            if len(found) >= limit:
                break
    return found


def latest_for_project(project: Path) -> Optional[tuple[Path, dict]]:
    found = list_for_project(project, limit=1)
    return found[0] if found else None
