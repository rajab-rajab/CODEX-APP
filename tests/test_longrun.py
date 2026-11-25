"""Tests for the self-test, Codex's own /review, /history, auto-compact and /queue. Fake Codex only:
no key, nothing billed."""

import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import config, engine, selftest, sessions, updater  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex, FakeTurn, answer, ev, item  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")


def make_app(root, script=None, mode="single", **settings):
    import main
    codex = FakeCodex(script or {})
    return main.App(codex, sdk, root, Settings(**settings), mode), codex


# --- the self-test ------------------------------------------------------------------------------------

def test_the_offline_checks_pass_on_the_installed_sdk():
    results = selftest.run(live=False)
    assert results and all(ok for _, ok, _ in results), results


def test_a_missing_piece_fails_the_self_test(monkeypatch):
    import openai_codex
    monkeypatch.delattr(openai_codex, "SkillInput")
    results = dict((name, (ok, why)) for name, ok, why in selftest.run(live=False))
    ok, why = results["SDK: approval modes, sandboxes and inputs"]
    assert not ok and "SkillInput is missing" in why


def test_selftest_main_reports_and_exits(monkeypatch, capsys):
    monkeypatch.setattr(selftest, "run", lambda live=True, codex=None: [("a", True, ""), ("b", False, "gone")])
    assert selftest.main(quiet=True) == 1
    out = capsys.readouterr().out
    assert "FAIL  b: gone" in out and "ok    a" not in out


def test_update_uses_the_self_test(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return NS(returncode=1)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    assert updater._engine_starts() is False
    assert seen["cmd"][-2:] == ["--selftest", "--quiet"]


def test_a_failed_self_test_puts_the_old_engine_back(tmp_path):
    calls = []
    pip = lambda cmd, **kw: calls.append(cmd[-1]) or NS(returncode=0, stderr="")  # noqa: E731
    status = updater.EngineStatus(True, "x", "0.172.0", "0.180.0")
    ok, message = updater.engine_apply(tmp_path, status, run=pip, starts=lambda: False)
    assert not ok and calls == ["openai-codex==0.180.0", "openai-codex==0.172.0"] and "self-test" in message


# --- Codex's own /review --------------------------------------------------------------------------------

def test_review_targets():
    import main
    assert main.review_target([], "") == {"type": "uncommittedChanges"}
    assert main.review_target(["branch", "main"], "branch main") == {"type": "baseBranch", "branch": "main"}
    assert main.review_target(["commit", "abc123"], "commit abc123") == {"type": "commit", "sha": "abc123"}
    assert main.review_target(["look", "at security"], "look at security") == \
        {"type": "custom", "instructions": "look at security"}


def test_review_uses_codex_own_review(tmp_path, monkeypatch, capsys):
    started = []
    review_events = [ev("item/completed", item=item(type="enteredReviewMode", review="uncommitted changes")),
                     ev("item/completed", item=item(type="exitedReviewMode", review="1. calc.py:2 subtracts instead of adding.")),
                     ev("turn/completed", turn=NS(status=NS(value="completed"), error=None))]

    def fake_start(codex, thread_id, target):
        started.append((thread_id, target))
        turn = FakeTurn(review_events)
        turn.id = "review-turn"
        return turn
    monkeypatch.setattr(engine, "start_review", fake_start)
    app, codex = make_app(tmp_path)
    app.command("/review")
    assert started == [("thr_1", {"type": "uncommittedChanges"})]
    assert codex.started[0]["sandbox"] == sdk.Sandbox.read_only
    assert "subtracts instead of adding" in capsys.readouterr().out


# --- /history -------------------------------------------------------------------------------------------

def thread(tid, preview, source="cli", updated=1_795_000_000):
    return NS(id=tid, name=None, preview=preview, source=NS(root=NS(value=source)), updated_at=updated, ephemeral=False)


def test_history_lists_and_carries_on(tmp_path, capsys):
    app, codex = make_app(tmp_path, {}, mode="dual")
    codex.thread_list = lambda cwd, limit: NS(data=[thread("t-cli", "fix the login page"),
                                                    thread("t-vs", "add dark mode", source="vscode")])
    app.command("/history")
    out = capsys.readouterr().out
    assert "1." in out and "cli" in out and "fix the login page" in out and "vscode" in out
    app.command("/history 2")
    assert app.mode.name == "single" and app.mode.agent.thread_id == "t-vs" and app.title == "add dark mode"
    app.command("/history 9")
    assert "Use /history" in capsys.readouterr().out


# --- auto-compact ---------------------------------------------------------------------------------------

def nearly_full(text):
    return answer(text)[:2] + [
        ev("thread/tokenUsage/updated", token_usage=NS(
            total=NS(input_tokens=1, cached_input_tokens=0, output_tokens=1, reasoning_output_tokens=0),
            last=NS(total_tokens=95_000), model_context_window=100_000)),
        ev("turn/completed", turn=NS(status=NS(value="completed"), error=None))]


def test_auto_compact_when_context_is_nearly_full(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [nearly_full("a")]})
    app.run_task("big job")
    assert codex.compacted == ["thr_1"] and "summarised the conversation" in capsys.readouterr().out


def test_auto_compact_can_be_turned_off(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [nearly_full("a")]}, auto_compact=False)
    app.run_task("big job")
    assert codex.compacted == [] and "/compact frees it up" in capsys.readouterr().out


# --- /queue ---------------------------------------------------------------------------------------------

def test_queue_while_busy_and_run_in_order(tmp_path, monkeypatch, capsys):
    import builtins
    app, codex = make_app(tmp_path, {1: [answer("one"), answer("two"), answer("three")]})
    app._busy = True
    assert app.take_line("/queue add tests") is True
    assert app.take_line("/queue update the README") is True
    app._busy = False
    assert "(queued; 2 waiting)" in capsys.readouterr().out
    app.command("/queue")
    assert "1. add tests" in capsys.readouterr().out

    def no_more_input(prompt=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", no_more_input)
    app.loop()  # runs the queue, then the input ends
    assert [t["text"] for t in codex.turns][-2:] == ["add tests", "update the README"]
    assert "Next from the queue (1 more waiting): add tests" in capsys.readouterr().out


def test_queue_clear(tmp_path, capsys):
    app, _ = make_app(tmp_path)
    app.command("/queue x")
    app.command("/queue clear")
    assert not app.queued and "The queue is empty" in capsys.readouterr().out


# --- fixes from the real run ----------------------------------------------------------------------------

@pytest.mark.parametrize("command,found", [
    ("Get-ChildItem -Force -Name | Where-Object { $_ -ne '.env' }", None),  # what Codex really ran
    ("rg --glob '!.env' TODO", None),
    ("git ls-files -- :!.env", None),
    ("grep -r x --exclude=.env .", None),
    ("Get-Content .env", ".env"),
    ("type .env | more", ".env"),
])
def test_leaving_a_secret_out_is_not_touching_it(command, found):
    from codex_app import protect
    assert protect.secret_in_command(command) == found


def test_review_is_shown_once(tmp_path, monkeypatch):
    events = []
    text = "1. calc.py:2 multiplies."
    review = [ev("item/agentMessage/delta", delta=text), ev("item/completed", item=item(type="agentMessage", text=text)),
              ev("item/completed", item=item(type="exitedReviewMode", review=text)),
              ev("turn/completed", turn=NS(status=NS(value="completed"), error=None))]
    turn = FakeTurn(review)
    turn.id = "r"
    monkeypatch.setattr(engine, "start_review", lambda codex, thread_id, target: turn)
    from codex_app.modes import make_reviewer
    reviewer = make_reviewer(FakeCodex({}), sdk, tmp_path, Settings(), "m",
                             on_event=lambda a, k, t: k != "answer" and events.append(t))  # "answer": not shown
    assert reviewer.run_review({"type": "uncommittedChanges"}) == text
    assert events.count(text) == 1


def test_helper_conversations_are_not_saved(tmp_path):
    from codex_app.modes import make_reviewer, make_writer
    codex = FakeCodex({1: [answer("x")], 2: [answer('{"subject": "s", "body": ""}')]})
    make_reviewer(codex, sdk, tmp_path, Settings(), "m").send("review")
    make_writer(codex, sdk, tmp_path, Settings(), "m").send("write")
    assert [s.get("ephemeral") for s in codex.started] == [True, True]


def test_a_turn_that_cannot_be_wound_back_gets_a_fresh_conversation(tmp_path):
    read_env = ev("item/started", item=NS(root=NS(type="commandExecution", command="Get-Content .env",
                                                  command_actions=[])))
    codex = FakeCodex({1: [[read_env] + answer("x")]})

    def refuse(method, params, response_model=None):
        raise sdk.InvalidRequestError(-32600, "turn does not have a persisted start boundary")
    codex._client = NS(request=refuse)
    events = []
    from codex_app.modes import SingleMode
    mode = SingleMode(codex, sdk, tmp_path, Settings(), on_event=lambda a, k, t: events.append(t))
    mode.run("x")
    assert mode.agent.thread_id is None and any("fresh conversation" in t for t in events)


def test_history_names_who_made_each_conversation(tmp_path, capsys):
    app, codex = make_app(tmp_path)
    codex.thread_list = lambda cwd, limit: NS(data=[
        NS(id="a", name=None, originator="codex_cli_rs", preview="fix it", source=None, updated_at=1, ephemeral=False),
        NS(id="b", name=None, originator="codex_python_sdk", source=None, updated_at=1, ephemeral=False,
           preview="[Conversation, not a coding task] Answer directly.\n\nwhat is a closure?")])
    app.command("/history")
    out = capsys.readouterr().out
    assert "Codex CLI   fix it" in out and "Codex App   what is a closure?" in out and "[Conversation" not in out


class CtrlCTurn(FakeTurn):
    """A turn during which the user presses Ctrl+C."""

    def stream(self):
        yield ev("item/agentMessage/delta", delta="working")
        raise KeyboardInterrupt


def test_ctrl_c_really_stops_the_turn_in_codex(tmp_path):
    """Before 1.6, the turn was forgotten before Codex was told to stop, so it carried on in the background."""
    from test_app import make_agent
    codex = FakeCodex({1: [[]]})
    agent = make_agent(codex, tmp_path)
    thread = agent._ensure_thread()
    turn = CtrlCTurn([])
    thread.turn = lambda text, **options: turn
    with pytest.raises(KeyboardInterrupt):
        agent.send("long task")
    assert turn.interrupted and agent._turn is None


def test_helper_conversations_are_archived_after_use(tmp_path, capsys):
    import json
    app, codex = make_app(tmp_path, {1: [answer(json.dumps({"subject": "s", "body": ""}))]})
    archived = []
    codex.thread_archive = archived.append
    assert app._write("git commit message", "diff") == ("s", "")
    assert archived == ["thr_1"] and app.writer.thread_id is None
