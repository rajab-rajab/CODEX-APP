"""/export: a conversation as a Markdown file, read back from Codex itself.

It holds your messages, Codex's answers, the commands it ran (with the end of their output) and the
files it changed (with their diffs). In dual and team mode each agent gets its own section."""

import re
import time
from pathlib import Path
from typing import Optional

from . import config

OUTPUT_LINES = 15  # lines of each command's output kept in the export


def _root(x):
    return getattr(x, "root", x)


def _value(x):
    return getattr(x, "value", x)


_GUIDANCE = re.compile(r"^\s*\[[^\]]{0,60}\][^\n]*\n+")


def _user_text(item) -> str:
    parts = []
    for piece in getattr(item, "content", None) or []:
        piece = _root(piece)
        kind = getattr(piece, "type", "")
        if kind == "text":
            parts.append(_GUIDANCE.sub("", str(getattr(piece, "text", ""))))
        elif kind in ("image", "localImage"):
            parts.append(f"*[image: {getattr(piece, 'path', None) or getattr(piece, 'url', '')}]*")
        elif kind in ("mention", "skill"):
            parts.append(f"*[{kind}: {getattr(piece, 'name', '')}]*")
    return "\n\n".join(p for p in parts if p.strip())


def _fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text.rstrip()}\n{ticks}"


def render_item(item) -> Optional[str]:
    item = _root(item)
    kind = getattr(item, "type", "")
    if kind == "userMessage":
        text = _user_text(item)
        return f"### You\n\n{text}" if text else None
    if kind == "agentMessage":
        text = str(getattr(item, "text", "") or "").strip()
        return f"### Codex\n\n{text}" if text else None
    if kind == "exitedReviewMode":
        text = str(getattr(item, "review", "") or "").strip()
        return f"### Review\n\n{text}" if text else None
    if kind == "reasoning":
        summary = " ".join(getattr(item, "summary", None) or []).strip()
        return f"*Thinking: {summary}*" if summary else None
    if kind == "commandExecution":
        out = str(getattr(item, "aggregated_output", "") or "").rstrip().splitlines()
        code = getattr(item, "exit_code", None)
        shown = out[-OUTPUT_LINES:]
        block = f"$ {getattr(item, 'command', '')}"
        if shown:
            block += ("\n..." if len(out) > OUTPUT_LINES else "") + "\n" + "\n".join(shown)
        result = "did not run" if code is None else f"exit code {code}"
        return f"**Command** ({result})\n\n{_fence(block)}"
    if kind == "fileChange":
        pieces = []
        for change in getattr(item, "changes", None) or []:
            what = str(getattr(_root(getattr(change, "kind", "")), "type", "") or _value(getattr(change, "kind", ""))
                       or "update")
            diff = str(getattr(change, "diff", "") or "")
            pieces.append(f"**{what.capitalize()} `{getattr(change, 'path', '?')}`**" +
                          (f"\n\n{_fence(diff, 'diff')}" if diff.strip() else ""))
        return "\n\n".join(pieces) or None
    if kind == "webSearch":
        return f"*Web search: {getattr(item, 'query', '')}*"
    if kind == "mcpToolCall":
        return f"*Tool: {getattr(item, 'server', '')} {getattr(item, 'tool', '')}*".replace("  ", " ")
    return None


def render(agents, title: str, project, mode: str) -> str:
    """Markdown for every agent's conversation. `agents` are (name, thread) pairs; thread.read() is the
    SDK's Thread.read."""
    lines = [f"# {title or 'Codex App conversation'}", "",
             f"*Project `{project}`, {mode} mode, exported {time.strftime('%Y-%m-%d %H:%M')} by Codex App.*", ""]
    for name, thread in agents:
        data = thread.read(include_turns=True)
        turns = getattr(getattr(data, "thread", data), "turns", None) or []
        if len(agents) > 1:
            lines += [f"## {name}", ""]
        for turn in turns:
            for item in getattr(turn, "items", None) or []:
                text = render_item(item)
                if text:
                    lines += [text, ""]
    return "\n".join(lines).rstrip() + "\n"


def default_path(project) -> Path:
    name = re.sub(r"[^\w.-]+", "-", Path(project).name or "project").strip("-") or "project"
    return config.APP_HOME / "exports" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.md"
