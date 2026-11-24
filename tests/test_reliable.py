"""Tests for automatic retries, limit warnings, /commit, /pr, /save and the finish signal. Fake Codex and
throwaway git repositories: no key, nothing billed, nothing pushed anywhere real."""

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import config, engine, gitflow, prefs, sessions  # noqa: E402
from codex_app.agent import CARRY_ON  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import SingleMode  # noqa: E402
from test_app import FakeCodex, answer, ev, make_agent  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")


def failed(info):
    return [ev("turn/completed", turn=NS(status=NS(value="failed"),
                                        error=NS(message=f"{info} happened", codex_error_info=NS(root=NS(value=info)))))]


# --- automatic retries --------------------------------------------------------------------------------

def test_a_busy_server_is_retried_without_repeating_the_message(tmp_path):
    events, waits = [], []
    codex = FakeCodex({1: [failed("serverOverloaded"), answer("Done.")]})
    agent = make_agent(codex, tmp_path, events)
    agent.sleep = waits.append
    assert agent.send("fix the bug") == "Done."
    assert [t["text"] for t in codex.turns] == ["fix the bug", CARRY_ON]
    assert waits == [5] and any("trying again in 5 s" in t for k, t in events if k == "notice")


def test_limits_and_bad_requests_are_not_retried(tmp_path):
    codex = FakeCodex({1: [failed("usageLimitExceeded")]})
    agent = make_agent(codex, tmp_path)
    agent.sleep = lambda s: pytest.fail("must not wait")
    assert "usageLimitExceeded happened" in agent.send("x")
    assert agent.last_failure == "usageLimitExceeded" and len(codex.turns) == 1


def test_retries_give_up_after_the_last_one(tmp_path):
    codex = FakeCodex({1: [failed("serverOverloaded")] * 4})
    agent = make_agent(codex, tmp_path)
    waits = []
    agent.sleep = waits.append
    assert "could not finish" in agent.send("x")
    assert waits == [5, 15, 45] and len(codex.turns) == 4


def test_a_turn_that_cannot_start_is_retried(tmp_path):
    codex = FakeCodex({1: [answer("ok")]})
    agent = make_agent(codex, tmp_path)
    agent.sleep = lambda s: None
    thread = agent._ensure_thread()
    real_turn, calls = thread.turn, []

    def flaky(text, **options):
        calls.append(text)
        if len(calls) == 1:
            raise sdk.ServerBusyError(-32001, "server overloaded", {"codex_error_info": "server_overloaded"})
        return real_turn(text, **options)
    thread.turn = flaky
    assert agent.send("x") == "ok" and calls == ["x", "x"]  # it never started, so the same message is fine


def test_retries_can_be_turned_off(tmp_path):
    codex = FakeCodex({1: [failed("serverOverloaded")]})
    SingleMode(codex, sdk, tmp_path, Settings(retries=0)).run("x")
    assert len(codex.turns) == 1


# --- limits -------------------------------------------------------------------------------------------

def snapshot(used=85, plan="plus", reached=None):
    return NS(plan_type=NS(value=plan), primary=NS(used_percent=used, window_duration_mins=300,
                                                    resets_at=int(time.time()) + 3600 + 30),
              secondary=NS(used_percent=10, window_duration_mins=10080, resets_at=None),
              credits=None, rate_limit_reached_type=reached)


def test_describe_limits():
    lines, warnings = engine.describe_limits(snapshot())
    assert lines[0] == "plan: plus" and "85% of your 5-hour limit used, resets in 1 h 0 min" in lines[1]
    assert "10% of your 7-day limit used" in lines[2]
    assert warnings == [lines[1]]
    assert engine.describe_limits(snapshot(used=20))[1] == []


def make_app(root, script=None, **settings):
    import main
    codex = FakeCodex(script or {})
    codex._client = NS(request=lambda method, params, response_model=None:
                       NS(rate_limits=codex.limits) if method == "account/rateLimits/read" else None)
    codex.limits = snapshot(used=20)
    return main.App(codex, sdk, root, Settings(**settings), "single"), codex


def test_warning_after_a_task_when_a_limit_is_nearly_used(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [answer("a"), answer("b")]})
    app.run_task("hello")
    assert "Careful" not in capsys.readouterr().out
    codex.limits = snapshot(used=92)
    app.run_task("hello again")
    assert "Careful: 92% of your 5-hour limit used" in capsys.readouterr().out


def test_hitting_a_limit_says_when_it_resets(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [failed("usageLimitExceeded")]})
    codex.limits = snapshot(used=100, reached="primary")
    app.run_task("hello")
    assert "You've hit a usage limit" in capsys.readouterr().out


def test_status_shows_limits(tmp_path, capsys):
    app, codex = make_app(tmp_path)
    codex.account = lambda: NS(account=None)
    app.command("/status")
    assert "Limits:      plan: plus; 20% of your 5-hour limit used" in capsys.readouterr().out


# --- /commit ------------------------------------------------------------------------------------------

def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def ident(cwd):
    git(cwd, "config", "user.name", "Test")
    git(cwd, "config", "user.email", "test@example.com")


def _build_repo(root: Path):
    git(root, "init", "-q", "-b", "main")
    ident(root)
    (root / "a.txt").write_text("a1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")


@pytest.fixture
def repo(tmp_path, git_template):
    return git_template("site", _build_repo, tmp_path)


def test_commit_writes_the_message_and_never_commits_secrets(repo, capsys):
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")  # not in .gitignore
    message = json.dumps({"subject": "Update a and add b", "body": "- a.txt: a2\n- b.txt: new"})
    app, codex = make_app(repo, {1: [answer(message)]})
    app.ask = lambda q: "y"
    assert app.commit() is True
    out = capsys.readouterr().out
    assert "Not committed, because they may hold secrets: .env" in out and "Update a and add b" in out
    assert git(repo, "log", "-1", "--format=%s%n%b").strip() == "Update a and add b\n- a.txt: a2\n- b.txt: new"
    assert git(repo, "status", "--porcelain") == "?? .env\n"  # the secret is left out
    writer_start = codex.started[0]
    assert writer_start["sandbox"] == sdk.Sandbox.read_only and "+a2" in codex.turns[0]["text"]
    assert codex.turns[0]["options"]["output_schema"]["required"] == ["subject", "body"]


def test_commit_with_your_own_message_or_an_edit(repo):
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    app, codex = make_app(repo)
    answers = iter(["e", "My own words"])
    app.ask = lambda q: next(answers)
    assert app.commit("Ignored suggestion") is True
    assert git(repo, "log", "-1", "--format=%s").strip() == "My own words" and codex.turns == []


def test_commit_needs_a_yes(repo, capsys):
    (repo / "a.txt").write_text("a2\n", encoding="utf-8")
    app, _ = make_app(repo)
    app.ask = lambda q: "n"
    assert app.commit("x") is False and "Nothing was committed" in capsys.readouterr().out
    assert git(repo, "status", "--porcelain") == " M a.txt\n"


def test_commit_in_a_subfolder_of_a_repository(repo):
    sub = repo / "app"
    sub.mkdir()
    (sub / "x.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "a.txt").write_text("outside the project\n", encoding="utf-8")
    app, _ = make_app(sub)
    app.ask = lambda q: "y"
    assert app.commit("Add x") is True
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["app/x.py"]  # a.txt isn't ours


# --- /pr ----------------------------------------------------------------------------------------------

def _build_cloned(base: Path):
    remote = base / "github.git"
    git(base, "init", "-q", "--bare", "-b", "main", str(remote))
    clone = base / "work"
    git(base, "clone", "-q", str(remote), str(clone))
    ident(clone)
    git(clone, "checkout", "-q", "-b", "main")
    (clone / "a.txt").write_text("a1\n", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "start")
    git(clone, "remote", "set-url", "origin", "../github.git")  # relative, so copies push to their own copy
    git(clone, "push", "-q", "-u", "origin", "main")
    git(clone, "remote", "set-head", "origin", "main")


@pytest.fixture
def cloned(tmp_path, git_template):
    """(clone, bare 'GitHub' remote) with main pushed."""
    base = git_template("cloned", _build_cloned, tmp_path)
    return base / "work", base / "github.git"


def test_pr_moves_commits_off_main_pushes_and_opens_it(cloned, monkeypatch, capsys):
    clone, remote = cloned
    (clone / "a.txt").write_text("a2\n", encoding="utf-8")
    git(clone, "commit", "-qam", "Fix the greeting")
    opened = []
    monkeypatch.setattr(gitflow, "gh_available", lambda: True)
    monkeypatch.setattr(gitflow, "create_pr", lambda root, base, branch, title, body:
                        opened.append((base, branch, title)) or "https://github.com/x/y/pull/1")
    app, _ = make_app(clone)
    app.ask = lambda q: "y"
    app.command("/pr")
    assert opened == [("main", "codex/fix-the-greeting", "Fix the greeting")]
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "codex/fix-the-greeting"
    assert git(clone, "rev-parse", "main") == git(clone, "rev-parse", "origin/main")  # main back in line
    assert "Fix the greeting" in git(remote, "log", "--format=%s", "codex/fix-the-greeting")  # pushed
    assert "pull/1" in capsys.readouterr().out


def test_pr_needs_a_yes_and_commits(cloned, monkeypatch, capsys):
    clone, remote = cloned
    monkeypatch.setattr(gitflow, "gh_available", lambda: True)
    app, _ = make_app(clone)
    app.command("/pr")
    assert "no commits to put in a pull request" in capsys.readouterr().out
    git(clone, "commit", "-q", "--allow-empty", "-m", "Empty")
    app.ask = lambda q: "n"
    app.command("/pr")
    assert "Nothing was pushed" in capsys.readouterr().out
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_pr_without_gh(cloned, monkeypatch, capsys):
    monkeypatch.setattr(gitflow, "gh_available", lambda: False)
    app, _ = make_app(cloned[0])
    app.command("/pr")
    assert "cli.github.com" in capsys.readouterr().out


# --- /save --------------------------------------------------------------------------------------------

def test_save_show_and_clear(tmp_path, capsys):
    import main
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "dual")
    app.command("/power max")
    app.command("/profile maths")
    app.command("/save")
    saved = prefs.load(tmp_path)
    assert saved["mode"] == "dual" and saved["power"] == "max" and saved["profile"] == "maths"
    assert saved["planner_effort"] == "high"  # the starting models; power max is applied on top at load
    s = Settings()
    prefs.apply(s, saved)
    assert (s.power, s.profile) == ("max", "maths")
    app.command("/save clear")
    assert prefs.load(tmp_path) == {}


# --- the finish signal ----------------------------------------------------------------------------------

def test_signal_done(monkeypatch, capsys):
    started = []
    proc = prefs.signal_done("All tests pass", popen=lambda cmd, **kw: started.append((cmd, kw)) or "proc")
    assert "\a" in capsys.readouterr().out
    if sys.platform == "win32":
        cmd, kw = started[0]
        assert cmd[0] == "powershell" and "All tests pass" in cmd[-1] and kw["stdin"] is subprocess.DEVNULL
        assert proc == "proc"


def test_long_tasks_signal_when_done(tmp_path, monkeypatch):
    signals = []
    monkeypatch.setattr(prefs, "signal_done", signals.append)
    app, _ = make_app(tmp_path, {1: [answer("a"), answer("b")]}, notify_after=0)
    app.run_task("build it")
    assert signals and "build it" in signals[0]
    app.settings.notify = False
    app.run_task("again")
    assert len(signals) == 1


def test_api_key_sign_in_has_no_plan_limits(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [answer("a"), answer("b")]})
    calls = []

    def refuse(method, params, response_model=None):
        calls.append(method)
        raise sdk.InvalidRequestError(-32600, "chatgpt authentication required to read rate limits")
    codex._client = NS(request=refuse)
    codex.account = lambda: NS(account=None)
    app.run_task("hello")
    app.run_task("again")
    app.command("/status")
    assert calls == ["account/rateLimits/read"]  # asked once, then remembered
    assert "Limits:      shown only when signed in with a ChatGPT account" in capsys.readouterr().out
