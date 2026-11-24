"""Tests for the power features: task profiles, power presets, structured verdicts, team mode, goals and
steering. Fake Codex only: no key, nothing billed."""

import json
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import goals, profiles, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import (VERDICT_SCHEMA, DualMode, SingleMode, TeamMode, TEAM_PLAN_SCHEMA,  # noqa: E402
                             group_parts)
from test_app import FakeCodex, answer, ev, item  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


def verdict(v="APPROVED", summary="Looks right.", fixes=()):
    return answer(json.dumps({"verdict": v, "summary": summary, "fixes": list(fixes)}))


# --- task profiles --------------------------------------------------------------------------------

@pytest.mark.parametrize("text,profile", [
    ("what is the capital of France?", "chat"),
    ("hello, how are you?", "chat"),
    ("explain what a closure is", "chat"),
    ("fix the login bug in app.py", "coding"),
    ("add a test for @calc.py", "coding"),
    ("write a function that lists primes", "coding"),
    ("solve x^2 - 5x + 6 = 0", "maths"),
    ("integrate sin(x) from 0 to pi", "maths"),
    ("what is 17 * 23?", "maths"),
    ("how many ways can 5 people sit in a row?", "maths"),
])
def test_detect(text, profile):
    assert profiles.detect(text) == profile


def test_effort_per_profile():
    assert profiles.effort_for("chat", "high") == "low"
    assert profiles.effort_for("chat", "minimal") == "minimal"  # never raised for chat
    assert profiles.effort_for("maths", "medium") == "high"
    assert profiles.effort_for("maths", "max") == "max"         # never lowered for maths
    assert profiles.effort_for("coding", "medium") == "medium"


def test_power_presets_and_back_to_normal():
    s = Settings(single_model="my-model", single_effort="low")
    start = Settings(**vars(s))
    profiles.apply_power(s, "max", start)
    assert (s.single_model, s.single_effort, s.planner_effort) == (profiles.STRONG_MODEL, "max", "max")
    profiles.apply_power(s, "normal", start)
    assert (s.single_model, s.single_effort, s.power) == ("my-model", "low", "normal")


def test_chat_is_answered_fast_and_maths_carefully(tmp_path):
    codex = FakeCodex({1: [answer("Paris."), answer("Answer: 391")]})
    mode = SingleMode(codex, sdk, tmp_path, Settings())
    mode.run("capital of France?", profile="chat")
    mode.run("17 * 23", profile="maths")
    chat, maths = codex.turns
    assert chat["options"]["effort"] == "low" and "Conversation" in chat["text"]
    assert maths["options"]["effort"] == "high" and "Answer:" in maths["text"] and "Python" in maths["text"]


def test_dual_mode_answers_chat_without_the_worker(tmp_path):
    codex = FakeCodex({1: [answer("Paris.")]})
    mode = DualMode(codex, sdk, tmp_path, Settings())
    assert mode.run("capital of France?", profile="chat") == "Paris."
    assert len(codex.started) == 1 and codex.turns[0]["options"]["effort"] == "low"


# --- structured verdicts --------------------------------------------------------------------------

def test_structured_verdict(tmp_path):
    events = []
    codex = FakeCodex({1: [answer("1. Fix add"), verdict()], 2: [answer("Changed calc.py.")]})
    mode = DualMode(codex, sdk, tmp_path, Settings(), on_event=lambda a, k, t: events.append((a, k, t)))
    assert "VERDICT: APPROVED" in mode.run("fix add")
    assert codex.turns[2]["options"]["output_schema"] == VERDICT_SCHEMA
    assert ("planner", "text", "Looks right.\nVERDICT: APPROVED") in events
    assert not any(k == "text" and t.startswith("{") for _, k, t in events)  # the raw JSON isn't shown


def test_structured_verdict_sends_fixes_back(tmp_path):
    codex = FakeCodex({1: [answer("plan"), verdict("CHANGES_NEEDED", "Missing test.", ["add a test for add()"]),
                           verdict()],
                       2: [answer("did it"), answer("added the test")]})
    DualMode(codex, sdk, tmp_path, Settings()).run("fix add")
    assert "- add a test for add()" in codex.turns[3]["text"]


def test_structured_verdicts_fall_back_to_text(tmp_path):
    class NoSchemaThread:
        def __init__(self, real):
            self.real, self.id = real, real.id

        def turn(self, text, **options):
            if "output_schema" in options:
                raise sdk.CodexError("output_schema not supported")
            return self.real.turn(text, **options)

    codex = FakeCodex({1: [answer("plan"), answer("ok\nVERDICT: APPROVED")], 2: [answer("did it")]})
    start = codex.thread_start
    codex.thread_start = lambda **o: NoSchemaThread(start(**o))
    settings = Settings()
    assert "APPROVED" in DualMode(codex, sdk, tmp_path, settings).run("fix add")
    assert settings.structured_verdicts is False  # remembered for the rest of the session


# --- team mode ------------------------------------------------------------------------------------

def test_group_parts():
    parts = [{"files": ["a.py"]}, {"files": ["b.py"]}, {"files": ["./A.py", "c.py"]}, {"files": []}, {"files": []}]
    groups = group_parts(parts, buckets=8)
    assert sorted(groups) == [[0, 2], [1], [3, 4]]  # a.py twice: together; no files listed: together
    assert sorted(len(g) for g in group_parts(parts, buckets=2)) == [2, 3]  # packed into two workers


def team_plan(*parts, answer_text="Two parts."):
    return answer(json.dumps({"answer": answer_text, "parts": [
        {"title": t, "instructions": f"do {t}", "files": f} for t, f in parts]}))


def test_team_mode_runs_parts_side_by_side(tmp_path):
    codex = FakeCodex({1: [team_plan(("api", ["api.py"]), ("ui", ["ui.js"])), verdict()],
                       2: [answer("did a part")], 3: [answer("did a part")]})
    mode = TeamMode(codex, sdk, tmp_path, Settings(team_size=3))
    assert "APPROVED" in mode.run("build the feature")
    assert codex.turns[0]["options"]["output_schema"] == TEAM_PLAN_SCHEMA
    assert [a.name for a in mode.agents()] == ["planner", "worker-1", "worker-2"]
    worker_texts = sorted(t["text"] for t in codex.turns if t["thread"] in ("thr_2", "thr_3"))
    assert "YOUR PART (api)" in worker_texts[0] and "YOUR PART (ui)" in worker_texts[1]
    review = codex.turns[-1]["text"]
    assert "part 1: api" in review and "part 2: ui" in review


def test_team_mode_question_needs_no_workers(tmp_path):
    codex = FakeCodex({1: [answer(json.dumps({"answer": "add() subtracts.", "parts": []}))]})
    mode = TeamMode(codex, sdk, tmp_path, Settings())
    assert mode.run("what does add do?") == "add() subtracts."
    assert len(codex.started) == 1


def test_team_mode_saves_and_resumes_its_workers(tmp_path):
    codex = FakeCodex({1: [team_plan(("a", ["a.py"]), ("b", ["b.py"])), verdict()],
                       2: [answer("x")], 3: [answer("x")]})
    mode = TeamMode(codex, sdk, tmp_path, Settings())
    mode.run("t")
    resumed = TeamMode(codex, sdk, tmp_path, Settings())
    resumed.load_state(json.loads(json.dumps(mode.state())))
    assert [a.name for a in resumed.agents()] == ["planner", "worker-1", "worker-2"]
    assert {a.thread_id for a in resumed.workers()} == {"thr_2", "thr_3"}


# --- steering -------------------------------------------------------------------------------------

def test_steering_goes_to_the_agent_at_work(tmp_path):
    from test_app import FakeTurn
    mode = DualMode(FakeCodex({}), sdk, tmp_path, Settings())
    assert mode.steer("also add a test") == []  # nothing is running
    mode.worker._turn = turn = FakeTurn([])
    assert mode.busy and mode.steer("also add a test") == ["worker"]
    assert turn.steered == ["also add a test"]


def test_typing_while_codex_works(tmp_path):
    import main
    from test_app import FakeTurn
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "single")
    assert app.take_line("next task") is False  # not busy: it is the next message
    app._busy = True
    app.mode.agent._turn = turn = FakeTurn([])
    assert app.take_line("use tabs, not spaces") is True and turn.steered == ["use tabs, not spaces"]
    assert app.take_line("/stop") is True and turn.interrupted


def test_approval_answers_come_through_the_pump(tmp_path):
    import main
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "single")
    lines = queue.Queue()
    app.pump = main.InputPump(app, read=lambda: lines.get(timeout=10))
    app._busy = True
    got = []
    t = threading.Thread(target=lambda: got.append(app.ask("Allow it? ")), daemon=True)  # asked from Codex's side
    t.start()
    for _ in range(100):  # the user answers once the question is on screen
        if app._answers is not None:
            break
        threading.Event().wait(0.05)
    lines.put("y")
    t.join(5)
    assert got == ["y"]


def test_input_pump_ends_on_eof(tmp_path):
    import main
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "single")
    feed = iter(["hello"])

    def read():
        try:
            return next(feed)
        except StopIteration:
            raise EOFError
    pump = main.InputPump(app, read=read)
    assert pump.get() == "hello" and pump.get() is None


def test_parse_budget():
    import main
    assert (main.parse_budget("500k"), main.parse_budget("1.5m"), main.parse_budget("20,000")) == (500_000, 1_500_000, 20_000)
    assert main.parse_budget("lots") is None and main.parse_budget("0") is None


# --- goals ----------------------------------------------------------------------------------------

class FakeGoal:
    def __init__(self, events):
        self.events, self.steered, self.interrupted = events, [], False

    def __iter__(self):
        return iter(self.events)

    def steer(self, message):
        self.steered.append(message)

    def interrupt(self):
        self.interrupted = True


def test_goal_runs_and_can_be_steered(tmp_path, monkeypatch):
    started = []

    def fake_start(codex, thread_id, objective, token_budget=None):
        started.append((thread_id, objective, token_budget))
        return FakeGoal([ev("item/completed", item=item(type="commandExecution", exit_code=0, aggregated_output="ok"))]
                        + answer("All tests pass now."))
    monkeypatch.setattr(goals, "start", fake_start)
    mode = SingleMode(FakeCodex({}), sdk, tmp_path, Settings())
    assert mode.run_goal("make the tests pass", 500_000) == "All tests pass now."
    assert started == [("thr_1", "make the tests pass", 500_000)]


def test_dual_goal_is_checked_by_the_planner(tmp_path, monkeypatch):
    monkeypatch.setattr(goals, "start", lambda c, t, o, token_budget=None: FakeGoal(answer("Done.")))
    codex = FakeCodex({2: [verdict()]})  # thread 1 is the worker's (the goal), thread 2 the planner's
    mode = DualMode(codex, sdk, tmp_path, Settings())
    assert "APPROVED" in mode.run_goal("make the tests pass")
    assert "GOAL:\nmake the tests pass" in codex.turns[0]["text"]


def test_goal_wiring_with_the_sdk_client():
    calls = []
    state = NS(current_turn=lambda: "turn-2", thread_id="thr")
    client = NS(
        start_goal_operation=lambda thread_id, objective: (calls.append(("start", thread_id, objective)) or (state, "turn-1")),
        request=lambda method, params, response_model=None: calls.append((method, params)),
        next_goal_notification=lambda s: None, unregister_goal_operation=lambda s: None,
        cancel_goal_operation=lambda s: calls.append(("cancel",)),
        turn_steer=lambda thread_id, turn_id, items: calls.append(("steer", turn_id, items)))
    goal = goals.start(NS(_client=client), "thr", "ship it", token_budget=200_000)
    goal.steer("hurry")
    goal.interrupt()
    assert calls == [("start", "thr", "ship it"), ("thread/goal/set", {"threadId": "thr", "tokenBudget": 200_000}),
                     ("steer", "turn-2", [{"type": "text", "text": "hurry"}]), ("cancel",)]


# --- commands -------------------------------------------------------------------------------------

def test_power_and_profile_commands(tmp_path, capsys):
    import main
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "dual")
    app.command("/power max")
    assert app.mode.planner.model == profiles.STRONG_MODEL and app.mode.worker.effort == "xhigh"
    app.command("/power normal")
    assert app.mode.worker.model == Settings().worker_model and app.mode.worker.effort == "medium"
    app.command("/profile maths")
    assert app.settings.profile == "maths"
    app.command("/profile silly")
    assert "Use /profile" in capsys.readouterr().out


def test_goal_command(tmp_path, capsys, monkeypatch):
    import main
    monkeypatch.setattr(goals, "start", lambda c, t, o, token_budget=None: FakeGoal(answer(f"{o} / {token_budget}")))
    app = main.App(FakeCodex({}), sdk, tmp_path, Settings(), "single")
    app.command("/goal --budget lots x")
    assert "Use /goal --budget" in capsys.readouterr().out
    app.command("/goal --budget 300k make it fast")
    assert "make it fast / 300000" in capsys.readouterr().out
