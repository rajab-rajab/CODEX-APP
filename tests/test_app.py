"""Tests for Codex App with a fake Codex, so no key is needed and nothing is billed."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openai_codex as sdk  # noqa: E402

from codex_app import sessions  # noqa: E402
from codex_app.agent import CodexAgent, Usage  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import DualMode, SingleMode, parse_verdict  # noqa: E402


# --- a fake Codex ---------------------------------------------------------------------------------

def ev(method, **payload):
    return NS(method=method, payload=NS(**payload))


def item(**fields):
    return NS(root=NS(**fields))


def answer(text, status="completed", usage=(100, 20, 30)):
    """The events of one turn: some streamed text, an agentMessage, usage, then turn/completed."""
    inp, cached, out = usage
    return [
        ev("item/agentMessage/delta", delta=text),
        ev("item/completed", item=item(type="agentMessage", text=text)),
        ev("thread/tokenUsage/updated", token_usage=NS(total=NS(input_tokens=inp, cached_input_tokens=cached,
                                                                output_tokens=out, reasoning_output_tokens=5))),
        ev("turn/completed", turn=NS(status=NS(value=status), error=None)),
    ]


class FakeTurn:
    def __init__(self, events):
        self.events = events
        self.interrupted = False

    def stream(self):
        yield from self.events

    def interrupt(self):
        self.interrupted = True


class FakeThread:
    def __init__(self, codex, thread_id, options):
        self.codex, self.id, self.options = codex, thread_id, options

    def turn(self, text, **options):
        self.codex.turns.append({"thread": self.id, "text": text, "options": options, "start": self.options})
        return FakeTurn(self.codex.script[self.id].pop(0))


class FakeCodex:
    """script: thread number (1, 2, ...) -> list of turns, each a list of events."""

    def __init__(self, script):
        self.script = {f"thr_{k}": v for k, v in script.items()}
        self.started, self.resumed, self.turns = [], [], []

    def thread_start(self, **options):
        thread_id = f"thr_{len(self.started) + 1}"
        self.started.append(options)
        return FakeThread(self, thread_id, options)

    def thread_resume(self, thread_id, **options):
        self.resumed.append(thread_id)
        return FakeThread(self, thread_id, options)


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


# --- one agent ------------------------------------------------------------------------------------

def make_agent(codex, tmp_path, events=None):
    return CodexAgent(codex, name="codex", model="gpt-5.3-codex", effort="medium",
                      sandbox=sdk.Sandbox.workspace_write, approval_mode=sdk.ApprovalMode.deny_all,
                      instructions="rules", cwd=tmp_path,
                      on_event=(lambda a, k, t: events.append((k, t))) if events is not None else None)


def test_thread_is_started_with_the_right_settings(tmp_path):
    codex = FakeCodex({1: [answer("hello")]})
    assert make_agent(codex, tmp_path).send("hi") == "hello"
    start = codex.started[0]
    assert start["model"] == "gpt-5.3-codex" and start["cwd"] == str(tmp_path)
    assert start["sandbox"] == sdk.Sandbox.workspace_write and start["approval_mode"] == sdk.ApprovalMode.deny_all
    assert start["developer_instructions"] == "rules"
    assert start["config"] == {"model_reasoning_effort": "medium"}


def test_messages_continue_the_same_thread(tmp_path):
    codex = FakeCodex({1: [answer("one"), answer("two")]})
    agent = make_agent(codex, tmp_path)
    agent.send("a")
    agent.send("b")
    assert len(codex.started) == 1 and [t["thread"] for t in codex.turns] == ["thr_1", "thr_1"]


def test_work_is_shown_on_screen(tmp_path):
    events = []
    turn = [
        ev("item/started", item=item(type="commandExecution", command="py -m pytest -q")),
        ev("item/completed", item=item(type="commandExecution", exit_code=1, aggregated_output="1 failed")),
        ev("item/started", item=item(type="fileChange", changes=[NS(path="calc.py")])),
        ev("item/completed", item=item(type="fileChange", status=NS(value="completed"))),
        ev("item/completed", item=item(type="reasoning", summary=["Checking the add function"])),
    ] + answer("Fixed.")
    make_agent(FakeCodex({1: [turn]}), tmp_path, events).send("fix")
    assert ("tool", "run: py -m pytest -q") in events
    assert ("tool_error", "exit code 1: 1 failed") in events
    assert ("tool", "edit: calc.py") in events and ("tool_result", "edit completed") in events
    assert ("thinking", "Checking the add function") in events
    assert ("text", "Fixed.") in events


def test_usage_and_cost(tmp_path, monkeypatch):
    agent = make_agent(FakeCodex({1: [answer("x", usage=(1_000_000, 200_000, 100_000))]}), tmp_path)
    agent.send("x")
    assert (agent.usage.input, agent.usage.cached, agent.usage.output) == (1_000_000, 200_000, 100_000)
    assert agent.usage.cost() is None  # no price configured
    from codex_app import agent as agent_module
    monkeypatch.setitem(agent_module.PRICES, "gpt-5.3-codex", (1.0, 0.1, 10.0))
    assert agent.usage.cost() == pytest.approx(0.8 + 0.02 + 1.0)
    assert Usage.from_dict(agent.usage.to_dict()).cost() == pytest.approx(1.82)


def test_failed_turn_is_reported(tmp_path):
    events = []
    failed = [ev("turn/completed", turn=NS(status=NS(value="failed"), error=NS(message="model not found")))]
    reply = make_agent(FakeCodex({1: [failed]}), tmp_path, events).send("x")
    assert "model not found" in reply and ("notice", "Codex could not finish: model not found") in events


def test_permission_change_goes_with_the_next_turn(tmp_path):
    codex = FakeCodex({1: [answer("a"), answer("b"), answer("c")]})
    agent = make_agent(codex, tmp_path)
    agent.send("1")
    agent.set_sandbox(sdk.Sandbox.read_only)
    agent.send("2")
    agent.send("3")
    assert "sandbox" not in codex.turns[0]["options"]
    assert codex.turns[1]["options"]["sandbox"] == sdk.Sandbox.read_only
    assert "sandbox" not in codex.turns[2]["options"]  # Codex keeps it for later turns


# --- modes ----------------------------------------------------------------------------------------

def test_single_mode(tmp_path):
    codex = FakeCodex({1: [answer("done")]})
    assert SingleMode(codex, sdk, tmp_path, Settings()).run("task") == "done"
    assert codex.started[0]["model"] == "gpt-6.2-sol"


def test_plan_permission_means_read_only(tmp_path):
    codex = FakeCodex({1: [answer("ok")]})
    SingleMode(codex, sdk, tmp_path, Settings(permission="plan")).run("look")
    assert codex.started[0]["sandbox"] == sdk.Sandbox.read_only


def test_parse_verdict():
    assert parse_verdict("ok\nVERDICT: APPROVED") == "APPROVED"
    assert parse_verdict("VERDICT: APPROVED\nVERDICT: CHANGES_NEEDED") == "CHANGES_NEEDED"
    assert parse_verdict("nothing") is None


def test_dual_mode_plan_work_review(tmp_path):
    s = Settings()
    # thread 1 = planner, thread 2 = worker
    codex = FakeCodex({1: [answer("1. Fix add in calc.py"), answer("Tests pass.\nVERDICT: APPROVED")],
                       2: [answer("Changed calc.py, tests pass.")]})
    mode = DualMode(codex, sdk, tmp_path, s)
    assert "APPROVED" in mode.run("fix add")
    planner_start, worker_start = codex.started
    assert planner_start["model"] == s.planner_model and planner_start["sandbox"] == sdk.Sandbox.read_only
    assert worker_start["model"] == s.worker_model and worker_start["sandbox"] == sdk.Sandbox.workspace_write
    assert "PLAN FROM THE PLANNER" in codex.turns[1]["text"] and "1. Fix add" in codex.turns[1]["text"]
    assert "WORKER'S REPORT" in codex.turns[2]["text"]


def test_dual_mode_sends_work_back(tmp_path):
    codex = FakeCodex({1: [answer("plan"), answer("VERDICT: CHANGES_NEEDED\n- add a test"), answer("VERDICT: APPROVED")],
                       2: [answer("did it"), answer("added test")]})
    assert parse_verdict(DualMode(codex, sdk, tmp_path, Settings()).run("task")) == "APPROVED"
    assert "ASKS FOR CHANGES" in codex.turns[3]["text"]


def test_dual_mode_question_skips_the_worker(tmp_path):
    codex = FakeCodex({1: [answer("add() subtracts.\nPLAN: NONE")]})
    assert "subtracts" in DualMode(codex, sdk, tmp_path, Settings()).run("what does add do?")
    assert len(codex.started) == 1  # the worker thread was never started


def test_dual_mode_stops_after_review_limit(tmp_path):
    no = answer("VERDICT: CHANGES_NEEDED\n- again")
    codex = FakeCodex({1: [answer("plan"), no, list(no)], 2: [answer("w1"), answer("w2")]})
    assert parse_verdict(DualMode(codex, sdk, tmp_path, Settings(max_review_rounds=1)).run("t")) == "CHANGES_NEEDED"


def test_permission_change_in_dual_mode_keeps_planner_read_only(tmp_path):
    codex = FakeCodex({})
    mode = DualMode(codex, sdk, tmp_path, Settings())
    mode.set_permission("full")
    assert mode.worker.sandbox == sdk.Sandbox.full_access and mode.planner.sandbox == sdk.Sandbox.read_only


# --- saving and resuming --------------------------------------------------------------------------

def test_save_and_resume_continues_the_codex_threads(tmp_path):
    codex = FakeCodex({1: [answer("plan\nPLAN: NONE"), answer("again\nPLAN: NONE")]})
    mode = DualMode(codex, sdk, tmp_path, Settings())
    mode.run("question")
    path = sessions.new_session_path("dual")
    sessions.save(path, tmp_path, mode.state(), "question")

    _, data = sessions.latest_for_project(tmp_path)
    resumed = DualMode(codex, sdk, tmp_path, Settings())
    resumed.load_state(json.loads(json.dumps(data)))
    resumed.run("follow up")
    assert codex.resumed == ["thr_1"] and len(codex.started) == 1
    assert resumed.planner.usage.input == 100


# --- the terminal app -----------------------------------------------------------------------------

def test_app_commands(tmp_path, capsys):
    import main
    codex = FakeCodex({1: [answer("hi")]})
    app = main.App(codex, sdk, tmp_path, Settings(), "single")
    app.command("/permission plan")
    assert app.settings.permission == "plan" and app.mode.agent.sandbox == sdk.Sandbox.read_only
    app.run_task("hello")
    assert sessions.latest_for_project(tmp_path) is not None
    assert app.command("/exit") is False
    assert "read only" in capsys.readouterr().out
