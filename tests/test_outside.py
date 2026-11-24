"""Tests for keeping outside tools (computer use, browsers, accounts) away from Codex App by default."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_app import outside  # noqa: E402
from codex_app.config import Settings  # noqa: E402

CONFIG = r'''
model = "gpt-6-astra"
notify = [ "C:\\x\\codex-computer-use.exe", "turn-ended" ]

[plugins."browser@openai-bundled"]
enabled = true
[plugins."slack@openai-curated"]
enabled = true
[plugins."pdf@openai-primary-runtime"]
enabled = true
[plugins."google-calendar@openai-curated"]
enabled = false

[mcp_servers.github]
command = "npx"
[mcp_servers.my_tests]
command = "py"
[mcp_servers."chrome.devtools"]
command = "npx"
'''


@pytest.fixture
def home(tmp_path):
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


@pytest.mark.skipif(sys.version_info < (3, 11), reason="reads config.toml with tomllib")
def test_outside_plugins_and_servers_are_switched_off(home):
    overrides, names = outside.overrides(home)
    assert "plugins.browser@openai-bundled.enabled=false" in overrides
    assert "plugins.slack@openai-curated.enabled=false" in overrides
    assert not any("pdf@" in o for o in overrides)  # file plugins stay
    assert not any("google-calendar" in o for o in overrides)  # already off: left alone
    assert "mcp_servers.github.enabled=false" in overrides and not any("my_tests" in o for o in overrides)
    assert 'mcp_servers."chrome.devtools".enabled=false' in overrides  # a name with a dot gets quotes
    assert "plugins.unified-computer-use@openai-bundled.enabled=false" in overrides  # the desktop app's own
    assert 'computer_use.default_app_access="deny"' in overrides
    assert "notify=[]" in overrides  # the computer-use hook after every turn
    assert "slack" in names and "browser" in names and "pdf" not in names


def test_without_a_config_file_the_desktop_apps_own_tools_are_still_switched_off(tmp_path):
    overrides, names = outside.overrides(tmp_path)
    assert "plugins.computer-use@openai-bundled.enabled=false" in overrides and "node_repl" in names
    assert "notify=[]" not in overrides  # nobody's own notify hook is touched


def test_a_users_own_notify_hook_is_kept(tmp_path):
    (tmp_path / "config.toml").write_text('notify = ["my-script.cmd"]\n', encoding="utf-8")
    assert "notify=[]" not in outside.overrides(tmp_path)[0]


class FakeSDK:
    """sdk.Codex that refuses servers this PC doesn't have, like the real engine."""

    class CodexError(Exception):
        pass

    class CodexConfig:
        def __init__(self, config_overrides=()):
            self.config_overrides = config_overrides

    def __init__(self, known_servers=()):
        self.known, self.started = set(known_servers), []

    def Codex(self, config=None):
        overrides = config.config_overrides if config else ()
        for o in overrides:
            if o.startswith("mcp_servers.") and o.split(".")[1] not in self.known:
                raise self.CodexError(f"Codex process closed stdout. stderr_tail=Error: invalid transport\n"
                                      f"in `mcp_servers.{o.split('.')[1]}`")
        self.started.append(overrides)
        return NS(overrides=overrides)


def test_start_codex_drops_a_server_this_pc_does_not_have(monkeypatch):
    import main
    monkeypatch.setattr(outside, "overrides", lambda: (("plugins.browser@x.enabled=false",
                                                        "mcp_servers.node_repl.enabled=false"), ["browser", "node_repl"]))
    sdk = FakeSDK(known_servers=())
    codex, names = main.start_codex(sdk, Settings(), ("model=gpt-x",))
    assert codex.overrides == ("plugins.browser@x.enabled=false", "model=gpt-x")  # your own -c settings last
    assert names == ["browser"]


def test_outside_tools_on_means_no_overrides(monkeypatch):
    import main
    monkeypatch.setattr(outside, "overrides", lambda: pytest.fail("must not switch anything off"))
    sdk = FakeSDK()
    codex, names = main.start_codex(sdk, Settings(outside_tools=True))
    assert codex.overrides == () and names == []


def test_other_errors_still_raise(monkeypatch):
    import main

    class Broken(FakeSDK):
        def Codex(self, config=None):
            raise self.CodexError("no engine installed")
    monkeypatch.setattr(outside, "overrides", lambda: (("mcp_servers.node_repl.enabled=false",), ["node_repl"]))
    with pytest.raises(Broken.CodexError, match="no engine"):
        main.start_codex(Broken(), Settings())
