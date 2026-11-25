"""Tests for /plugins (plugins.py), with a fake plugin marketplace: nothing is installed for real."""

import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import config, engine, outside, plugins, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex  # noqa: E402


def summary(name, marketplace, installed=False, enabled=None, text="", availability="AVAILABLE", policy="AVAILABLE"):
    return NS(id=f"{name}@{marketplace}", name=name, installed=installed,
              enabled=installed if enabled is None else enabled, keywords=["kw-" + name],
              interface=NS(short_description=text), availability=NS(value=availability),
              install_policy=NS(value=policy), disabled_reason=None)


MARKET = [
    NS(name="openai-bundled", path=NS(root="C:/codex/bundled"), plugins=[
        summary("latex", "openai-bundled", text="Compile LaTeX with TeX tools"),
        summary("browser", "openai-bundled", installed=True, text="Control the in-app browser"),
        summary("visualize", "openai-bundled", installed=True, text="Create interactive visuals")]),
    NS(name="openai-api-curated", path=NS(root="C:/codex/curated"), plugins=[
        summary("superpowers", "openai-api-curated", text="Plan, develop, and debug code"),
        summary("sentry", "openai-api-curated", text="Inspect recent Sentry issues"),
        summary("hookish", "openai-api-curated", text="Runs a hook"),
        summary("latex", "openai-api-curated", text="Another LaTeX"),
        summary("locked", "openai-api-curated", text="Admins only", availability="DISABLED_BY_ADMIN")]),
]
DETAILS = {
    "latex": dict(skills=["latex:compile"]), "superpowers": dict(skills=["tdd", "debugging"]),
    "sentry": dict(skills=["sentry"], mcp_servers=["sentry"]), "visualize": dict(skills=["viz"]),
    "browser": dict(skills=["browser"], hooks=[NS(key="browser:stop")]), "hookish": dict(hooks=[NS(key="h")]),
    "locked": dict(skills=["x"]),
}


class Market:
    def __init__(self):
        self.calls = []

    def __call__(self, codex, method, params, model_name):
        self.calls.append((method, params))
        if method == "plugin/list":
            return NS(marketplaces=MARKET)
        if method == "plugin/read":
            d = {"skills": [], "mcp_servers": [], "apps": [], "hooks": [], "scheduled_tasks": [],
                 **DETAILS[params["pluginName"]]}
            return NS(plugin=NS(description=f"About {params['pluginName']}.",
                                skills=[NS(name=s) for s in d["skills"]], mcp_servers=d["mcp_servers"],
                                apps=[NS(name=a) for a in d["apps"]], hooks=d["hooks"],
                                scheduled_tasks=d["scheduled_tasks"]))
        if method == "plugin/install":
            return NS(apps_needing_auth=[])
        if method == "plugin/uninstall":
            return NS()
        raise AssertionError(method)


@pytest.fixture
def market(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    fake = Market()
    monkeypatch.setattr(engine, "_request", fake)
    return fake


def app(tmp_path, answer="y", **settings):
    import main
    a = main.App(FakeCodex({}), sdk, tmp_path, Settings(**settings), "single")
    a.ask = lambda q: answer
    return a


# --- plugins.py ---------------------------------------------------------------------------------------

def test_catalogue_find_and_search(market):
    listed = plugins.catalogue(None, "C:/project")
    assert market.calls[0] == ("plugin/list", {"cwds": ["C:/project"]})
    assert [p.id for p in plugins.find(listed, "latex")] == ["latex@openai-bundled", "latex@openai-api-curated"]
    assert [p.id for p in plugins.find(listed, "LATEX@openai-api-curated")] == ["latex@openai-api-curated"]
    assert [p.name for p in plugins.search(listed, "sentry issues")] == ["sentry"]
    assert [p.name for p in plugins.search(listed, "kw-superpowers")] == ["superpowers"]  # keywords count
    locked = plugins.find(listed, "locked")[0]
    assert not locked.available and plugins.find(listed, "latex")[0].marketplace_path == "C:/codex/bundled"


def test_what_reaches_outside(market):
    listed = plugins.catalogue(None)
    reasons = {name: plugins.read(None, plugins.find(listed, name)[0]).reasons_outside
               for name in ("superpowers", "sentry", "hookish", "browser", "visualize")}
    assert reasons["superpowers"] == [] and reasons["visualize"] == []
    assert reasons["sentry"] == ["MCP servers: sentry"] and reasons["hookish"] == ["1 hook(s) that run code on events"]
    assert reasons["browser"] == ["1 hook(s) that run code on events"]
    assert ("plugin/read", {"pluginName": "sentry", "marketplacePath": "C:/codex/curated"}) in market.calls


def test_recorded_outside_plugins_are_switched_off_at_start(market, tmp_path):
    plugins.record("sentry@openai-api-curated", True)
    overrides, names = outside.overrides(tmp_path / "no-codex-home")
    assert "plugins.sentry@openai-api-curated.enabled=false" in overrides and "sentry" in names
    plugins.record("sentry@openai-api-curated", False)
    assert plugins.recorded() == []


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# --- /plugins -----------------------------------------------------------------------------------------

def test_list_marks_what_is_off_in_codex_app(market, tmp_path, capsys):
    app(tmp_path).command("/plugins")
    out = plain(capsys.readouterr().out)
    assert "browser (openai-bundled)" in out and "off in Codex App" in out.split("browser")[1].split("\n")[0]
    assert "visualize (openai-bundled)  on" in out and "latex" not in out  # only installed ones


def test_available_lists_what_can_be_installed(market, tmp_path, capsys):
    app(tmp_path).command("/plugins available tex")
    out = plain(capsys.readouterr().out)
    assert "latex (openai-bundled)" in out and "latex (openai-api-curated)" in out and "visualize" not in out


def test_install_a_files_only_plugin_after_a_yes(market, tmp_path, capsys):
    app(tmp_path).command("/plugins install superpowers")
    out = capsys.readouterr().out
    assert "Works with your files only" in out and "Installed superpowers@openai-api-curated" in out
    assert ("plugin/install", {"pluginName": "superpowers", "marketplacePath": "C:/codex/curated"}) in market.calls
    assert "official Codex apps share" in out and plugins.recorded() == []


def test_no_means_no(market, tmp_path, capsys):
    app(tmp_path, answer="n").command("/plugins install superpowers")
    assert "Not installed" in capsys.readouterr().out and not any(m == "plugin/install" for m, _ in market.calls)


def test_plugins_that_reach_outside_need_outside_tools(market, tmp_path, capsys):
    for name in ("sentry", "hookish"):
        app(tmp_path).command(f"/plugins install {name}")
        out = capsys.readouterr().out
        assert "Reaches outside the project" in out and "Not installed" in out and "--outside-tools" in out
    assert not any(m == "plugin/install" for m, _ in market.calls)
    app(tmp_path, outside_tools=True).command("/plugins install sentry")
    out = capsys.readouterr().out
    assert "Installed sentry@openai-api-curated" in out and "switched off again at a normal start" in out
    assert plugins.recorded() == ["sentry@openai-api-curated"]


def test_ambiguous_unknown_unavailable_and_installed(market, tmp_path, capsys):
    a = app(tmp_path)
    a.command("/plugins install latex")
    assert "several marketplaces" in capsys.readouterr().out
    a.command("/plugins install nope")
    assert "No plugin called 'nope'" in capsys.readouterr().out
    a.command("/plugins install locked")
    assert "can't be installed here" in capsys.readouterr().out
    a.command("/plugins install visualize")
    assert "installed already" in capsys.readouterr().out
    assert not any(m == "plugin/install" for m, _ in market.calls)


def test_remove_after_a_yes(market, tmp_path, capsys):
    plugins.record("browser@openai-bundled", True)
    app(tmp_path).command("/plugins remove browser")
    out = capsys.readouterr().out
    assert "Removed browser@openai-bundled" in out and ("plugin/uninstall", {"pluginId": "browser@openai-bundled"}) \
        in market.calls and plugins.recorded() == []
    app(tmp_path).command("/plugins remove latex@openai-bundled")
    assert "isn't installed" in capsys.readouterr().out


def test_removing_a_files_only_plugin_writes_no_record(market, tmp_path):
    DETAILS["visualize"] = dict(skills=["viz"])
    app(tmp_path).command("/plugins remove visualize")
    assert not (config.APP_HOME / "outside-plugins.json").exists()
