"""Keeping Codex App's conversations to the project: plugins that reach outside it are off by default.

Codex App uses the same Codex engine and settings (~/.codex/config.toml) as the official Codex apps. If
one of them installed plugins that control the computer (computer use), a browser, or one of your
accounts (Slack, Google Calendar, mail ...), Codex App's conversations would get those tools too. They
work outside the sandbox, so `workspace` permission, the secret guard and the system-folder protection
don't cover them. (Found on a real PC: the computer-use tool describes itself as able to "control native
apps or browsers on the user's computer", yet is marked read-only, so approvals don't stop it.)

So, unless you start the app with --outside-tools (CODEX_APP_OUTSIDE_TOOLS=yes), Codex App starts the
engine with those plugins and servers switched off, for its own conversations only. Your settings file
isn't changed, and the official apps keep them. Plugins that only work with files (PDF, documents,
spreadsheets, presentations) stay on."""

import os
import re
import sys
from pathlib import Path

# A plugin or server whose name matches this reaches outside the project.
OUTSIDE = re.compile(r"computer|browser|chrome|\bcua\b|cua_|node_repl|slack|calendar|gmail|mail|outlook|teams|"
                     r"google-drive|gdrive|notion|linear|jira|github|whatsapp|telegram|discord|twitter|x-com",
                     re.IGNORECASE)
# Added by the official desktop app outside config.toml, so they're named here as well.
BUILT_IN_SERVERS = ("node_repl",)
BUILT_IN_PLUGINS = ("unified-computer-use@openai-bundled", "computer-use@openai-bundled",
                    "browser@openai-bundled", "chrome@openai-bundled")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _read_config(path: Path) -> dict:
    if sys.version_info < (3, 11):
        return {}
    import tomllib
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _key(name: str) -> str:
    # A plain name goes as is; one with dots or spaces needs quotes (quotes around a plain name would
    # make the engine create a new setting with the quotes in its name).
    return name if re.fullmatch(r"[A-Za-z0-9_@-]+", name) else '"' + name.replace('"', '\\"') + '"'


def overrides(home: Path = None) -> tuple[tuple, list]:
    """(engine launch overrides, what they switch off, for messages) to keep Codex to the project."""
    config = _read_config((home or codex_home()) / "config.toml")
    plugins = {name for name, value in (config.get("plugins") or {}).items()
               if isinstance(value, dict) and value.get("enabled", True) and OUTSIDE.search(name)}
    plugins |= set(BUILT_IN_PLUGINS)
    servers = {name for name, value in (config.get("mcp_servers") or {}).items()
               if isinstance(value, dict) and value.get("enabled", True) and OUTSIDE.search(name)}
    servers |= set(BUILT_IN_SERVERS)
    out = [f"plugins.{_key(p)}.enabled=false" for p in sorted(plugins)]
    out += [f"mcp_servers.{_key(s)}.enabled=false" for s in sorted(servers)]
    out.append('computer_use.default_app_access="deny"')  # a second lock, if a computer-use tool slips through
    notify = config.get("notify")
    if isinstance(notify, list) and any("computer-use" in str(part).lower() for part in notify):
        out.append("notify=[]")  # the desktop app's computer-use hook would run after every Codex App turn
    names = sorted({re.sub(r"@.*$", "", p) for p in plugins} | servers)
    return tuple(out), names
