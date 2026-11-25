"""/plugins: Codex's plugin marketplace from Codex App, like the official Codex apps' plugin directory.

A plugin bundles skills (instructions for tasks such as PDFs, LaTeX or test-driven development) and can
also bundle MCP servers, app integrations (Slack, Gmail ...), hooks (code that runs on events) and
scheduled tasks. Codex App sorts them into two kinds:

- **Works with your files**: skills only. Installing one is like adding instructions; it stays within the
  project's sandbox like everything else Codex does.
- **Reaches outside the project**: anything with MCP servers, apps, hooks or scheduled tasks, or a name
  that outside.py already treats as an outside tool. These talk to services or run code outside the
  sandbox, which is why Codex App keeps such tools off unless you start it with --outside-tools (see
  outside.py). So they can only be installed from Codex App with --outside-tools, and every one installed
  that way is recorded in ~/.codex-app/outside-plugins.json, so a normal start switches it off again.

Plugins are installed into Codex's own settings (~/.codex), which the official Codex apps share: a plugin
installed here is installed there too, and the other way round. Installs and removals ask first."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config, engine, outside


@dataclass
class Plugin:
    id: str                    # name@marketplace
    name: str
    marketplace: str
    installed: bool
    enabled: bool
    summary: str = ""
    keywords: list = field(default_factory=list)
    marketplace_path: Optional[str] = None
    available: bool = True      # False when an admin or the plan doesn't allow it
    why_not: str = ""


@dataclass
class Detail:
    plugin: Plugin
    description: str = ""
    skills: list = field(default_factory=list)       # skill names
    mcp_servers: list = field(default_factory=list)
    apps: list = field(default_factory=list)          # app names
    hooks: list = field(default_factory=list)         # hook names
    scheduled_tasks: int = 0

    @property
    def reasons_outside(self) -> list:
        """Why this plugin reaches outside the project ([] if it works with files only)."""
        why = []
        if self.mcp_servers:
            why.append("MCP servers: " + ", ".join(self.mcp_servers))
        if self.apps:
            why.append("app integrations: " + ", ".join(self.apps))
        if self.hooks:
            why.append(f"{len(self.hooks)} hook(s) that run code on events")
        if self.scheduled_tasks:
            why.append(f"{self.scheduled_tasks} scheduled task(s)")
        if outside.OUTSIDE.search(self.plugin.name) and not why:
            why.append("its name is one of the outside tools Codex App switches off")
        return why


def _value(x):
    return getattr(x, "value", getattr(x, "root", x))


def _path(p) -> Optional[str]:
    p = getattr(p, "root", p)
    return str(p) if p else None


def _summary_of(p, marketplace) -> Plugin:
    iface = getattr(p, "interface", None)
    text = ""
    for attr in ("short_description", "description", "long_description"):
        text = str(getattr(iface, attr, "") or "") if iface is not None else ""
        if text:
            break
    reason = getattr(p, "disabled_reason", None)
    return Plugin(id=p.id, name=p.name, marketplace=marketplace.name, installed=bool(p.installed),
                  enabled=bool(p.enabled), summary=" ".join(text.split()), keywords=list(p.keywords or []),
                  marketplace_path=_path(getattr(marketplace, "path", None)),
                  available=str(_value(getattr(p, "availability", "AVAILABLE"))) == "AVAILABLE"
                  and str(_value(getattr(p, "install_policy", "AVAILABLE"))) != "NOT_AVAILABLE",
                  why_not=str(_value(reason) or "") if reason else "")


def catalogue(codex, cwd: Optional[str] = None) -> list:
    """Every plugin the engine knows: the official marketplaces, plus any in the project's folder."""
    params = {"cwds": [cwd]} if cwd else {}
    listed = engine._request(codex, "plugin/list", params, "PluginListResponse")
    return [_summary_of(p, m) for m in listed.marketplaces for p in m.plugins]


def find(plugins: list, wanted: str) -> list:
    """The plugins called `wanted` (a name, or name@marketplace)."""
    wanted = wanted.strip().lower()
    exact = [p for p in plugins if p.id.lower() == wanted]
    return exact or [p for p in plugins if p.name.lower() == wanted]


def search(plugins: list, words: str) -> list:
    words = words.lower().split()
    return [p for p in plugins if all(w in " ".join([p.name, p.summary, " ".join(p.keywords)]).lower()
                                      for w in words)]


def _locator(p: Plugin) -> dict:
    if p.marketplace_path:
        return {"pluginName": p.name, "marketplacePath": p.marketplace_path}
    return {"pluginName": p.name, "remoteMarketplaceName": p.marketplace}


def read(codex, p: Plugin) -> Detail:
    d = engine._request(codex, "plugin/read", _locator(p), "PluginReadResponse").plugin
    return Detail(plugin=p, description=" ".join(str(d.description or "").split()),
                  skills=[getattr(s, "name", str(s)) for s in d.skills or []],
                  mcp_servers=[str(s) for s in d.mcp_servers or []],
                  apps=[getattr(a, "name", str(a)) for a in d.apps or []],
                  hooks=[getattr(h, "key", str(h)) for h in d.hooks or []],
                  scheduled_tasks=len(d.scheduled_tasks or []))


def install(codex, p: Plugin) -> list:
    """Install a plugin. Returns the names of apps that still need you to sign in to them."""
    response = engine._request(codex, "plugin/install", _locator(p), "PluginInstallResponse")
    return [getattr(a, "name", str(a)) for a in getattr(response, "apps_needing_auth", None) or []]


def uninstall(codex, p: Plugin) -> None:
    engine._request(codex, "plugin/uninstall", {"pluginId": p.id}, "PluginUninstallResponse")


# --- plugins installed from Codex App that reach outside: switched off at a normal start -------------

def _recorded_file() -> Path:
    return config.APP_HOME / "outside-plugins.json"


def recorded() -> list:
    """Plugin IDs installed from Codex App (with --outside-tools) that reach outside the project."""
    try:
        data = json.loads(_recorded_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(x) for x in data if isinstance(x, str)] if isinstance(data, list) else []


def record(plugin_id: str, outside_plugin: bool) -> None:
    before = recorded()
    ids = [x for x in before if x != plugin_id] + ([plugin_id] if outside_plugin else [])
    if sorted(ids) == sorted(before):
        return  # nothing changes: don't write (or create) the file
    _recorded_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _recorded_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(ids), indent=1), encoding="utf-8")
    tmp.replace(_recorded_file())
