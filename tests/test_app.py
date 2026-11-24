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

    def compact(self):
        self.codex.compacted.append(self.id)

    def set_name(self, name):
        self.codex.names[self.id] = name


class FakeCodex:
    """script: thread number (1, 2, ...) -> list of turns, each a list of events."""

    def __init__(self, script):
        self.script = {f"thr_{k}": v for k, v in script.items()}
        self.started, self.resumed, self.turns = [], [], []
        self.forked, self.compacted, self.names = [], [], {}

    def thread_start(self, **options):
        thread_id = f"thr_{len(self.started) + 1}"
        self.started.append(options)
        return FakeThread(self, thread_id, options)

    def thread_resume(self, thread_id, **options):
        self.resumed.append(thread_id)
        return FakeThread(self, thread_id, options)

    def thread_fork(self, thread_id, **options):
        new_id = f"{thread_id}_fork"
        self.forked.append(thread_id)
        self.script.setdefault(new_id, [])
        return FakeThread(self, new_id, options)


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


def test_only_agents_md_counts_as_read_by_codex(tmp_path):
    from codex_app.modes import project_instructions
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert project_instructions(tmp_path)[0] == ""
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    assert project_instructions(tmp_path)[0] == "AGENTS.md"


# --- official Codex features ------------------------------------------------------------------------

def test_plan_and_diff_are_shown(tmp_path):
    events = []
    turn = [
        ev("turn/plan/updated", explanation="Fixing add", plan=[NS(step="Read calc.py", status=NS(value="completed")),
                                                               NS(step="Fix add", status=NS(value="inProgress")),
                                                               NS(step="Run tests", status=NS(value="pending"))]),
        ev("turn/diff/updated", diff="--- a/calc.py\n+++ b/calc.py\n-a - b\n+a + b\n"),
    ] + answer("Fixed.")
    make_agent(FakeCodex({1: [turn]}), tmp_path, events).send("fix")
    assert ("plan", "Fixing add\n[x] Read calc.py\n[>] Fix add\n[ ] Run tests") in events
    assert ("diff", "--- a/calc.py\n+++ b/calc.py\n-a - b\n+a + b\n") in events


def test_context_left(tmp_path):
    turn = answer("x")[:2] + [
        ev("thread/tokenUsage/updated", token_usage=NS(
            total=NS(input_tokens=900, cached_input_tokens=0, output_tokens=100, reasoning_output_tokens=0),
            last=NS(total_tokens=25_000), model_context_window=100_000)),
        ev("turn/completed", turn=NS(status=NS(value="completed"), error=None))]
    agent = make_agent(FakeCodex({1: [turn]}), tmp_path)
    agent.send("x")
    assert agent.usage.context_left() == 75
    assert Usage.from_dict(agent.usage.to_dict()).context_left() == 75


def test_model_switch_goes_with_the_next_turn(tmp_path):
    codex = FakeCodex({1: [answer("a"), answer("b"), answer("c")]})
    agent = make_agent(codex, tmp_path)
    agent.send("1")
    agent.set_model("gpt-6.2-astra", "high")
    agent.send("2")
    agent.send("3")
    assert "model" not in codex.turns[0]["options"]
    assert codex.turns[1]["options"] == {"effort": "high", "model": "gpt-6.2-astra"}
    assert "model" not in codex.turns[2]["options"] and codex.turns[2]["options"]["effort"] == "high"


def test_fork_compact_rename(tmp_path):
    codex = FakeCodex({1: [answer("a")]})
    mode = SingleMode(codex, sdk, tmp_path, Settings())
    assert mode.compact() is False and mode.fork() is False  # nothing to work on yet
    mode.run("hi")
    assert mode.compact() is True and codex.compacted == ["thr_1"]
    mode.rename("Login bug")
    assert codex.names == {"thr_1": "Login bug"}
    mode.fork()
    assert codex.forked == ["thr_1"] and mode.agent.thread_id == "thr_1_fork"


def test_attachments_are_sent_as_codex_input(tmp_path):
    codex = FakeCodex({1: [answer("a")]})
    image = sdk.LocalImageInput(str(tmp_path / "shot.png"))
    SingleMode(codex, sdk, tmp_path, Settings()).run("what is wrong here?", [image])
    sent = codex.turns[0]["text"]
    assert isinstance(sent, list) and sent[0] == sdk.TextInput("what is wrong here?") and sent[1] == image


def test_dual_mode_gives_attachments_to_planner_and_first_worker_turn(tmp_path):
    codex = FakeCodex({1: [answer("plan"), answer("VERDICT: CHANGES_NEEDED\n- x"), answer("VERDICT: APPROVED")],
                       2: [answer("did it"), answer("fixed")]})
    mention = sdk.MentionInput("calc.py", str(tmp_path / "calc.py"))
    DualMode(codex, sdk, tmp_path, Settings()).run("fix", [mention])
    planner_first, worker_first, _, worker_second, _ = [t["text"] for t in codex.turns]
    assert mention in planner_first and mention in worker_first and isinstance(worker_second, str)


def test_web_search_setting_goes_to_the_thread(tmp_path):
    codex = FakeCodex({1: [answer("a")]})
    SingleMode(codex, sdk, tmp_path, Settings(web_search=True)).run("x")
    assert codex.started[0]["config"] == {"model_reasoning_effort": "medium", "web_search": "live"}


def test_official_permission_names():
    Settings(permission="read-only").check()
    s = Settings(permission="danger-full-access")
    s.check()
    assert s.permission == "full"


def test_ask_mode_opens_threads_through_the_approval_path(tmp_path, monkeypatch):
    from codex_app import approvals
    codex = FakeCodex({1: [answer("a")]})
    opened = []

    def fake_open(c, thread_cls, thread_id=None, fork=False, **common):
        opened.append((thread_id, fork, common["sandbox"]))
        return c.thread_start(**common)
    monkeypatch.setattr(approvals, "open_thread", fake_open)
    SingleMode(codex, sdk, tmp_path, Settings(approval="ask")).run("x")
    assert opened == [(None, False, sdk.Sandbox.workspace_write)]
    assert "approval_mode" not in codex.started[0]


# --- the terminal app's new commands --------------------------------------------------------------

def make_app(tmp_path, script=None, mode="single", **settings):
    import main
    return main, main.App(FakeCodex(script or {}), sdk, tmp_path, Settings(**settings), mode)


def test_at_mentions_attach_files(tmp_path):
    import main
    (tmp_path / "calc.py").write_text("x", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"png")
    found = main.mentions_in("look at @calc.py and @shot.png, not @nobody or me@example.com", tmp_path, sdk)
    assert found == [sdk.MentionInput("calc.py", str(tmp_path / "calc.py")),
                     sdk.LocalImageInput(str(tmp_path / "shot.png"))]


def test_mentions_and_pending_attachments_go_with_the_message(tmp_path):
    (tmp_path / "calc.py").write_text("x", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(b"png")
    main, app = make_app(tmp_path, {1: [answer("ok")]})
    app.command("/image shot.png")
    app.run_task("fix @calc.py")
    sent = app.codex.turns[0]["text"]
    assert sent[0] == sdk.TextInput("fix @calc.py")
    assert sdk.LocalImageInput(str(tmp_path / "shot.png")) in sent
    assert sdk.MentionInput("calc.py", str(tmp_path / "calc.py")) in sent
    assert app.pending == []


def test_model_command(tmp_path, capsys):
    main, app = make_app(tmp_path, {1: [answer("a")]})
    app.command("/model gpt-6.2-astra xhigh")
    assert app.settings.single_model == "gpt-6.2-astra" and app.mode.agent.effort == "xhigh"
    app.command("/model gpt-6.2-astra silly")
    assert "Effort must be one of" in capsys.readouterr().out
    _, dual = make_app(tmp_path, mode="dual")
    dual.command("/model worker gpt-6.2-astra")
    assert dual.mode.worker.model == "gpt-6.2-astra" and dual.mode.planner.model == Settings().planner_model


def test_review_is_read_only_and_separate(tmp_path):
    main, app = make_app(tmp_path, {1: [answer("No problems found.")]})
    app.command("/review branch main")
    start = app.codex.started[0]
    assert start["sandbox"] == sdk.Sandbox.read_only and "REVIEWER" in start["developer_instructions"]
    assert "git diff main...HEAD" in app.codex.turns[0]["text"]
    assert app.mode.is_empty()  # the conversation itself is untouched


def test_diff_command_shows_changes_and_new_files(tmp_path, capsys):
    import subprocess
    main, app = make_app(tmp_path)
    app.command("/diff")
    assert "not a git repository" in capsys.readouterr().out
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "new.py").write_text("x", encoding="utf-8")
    app.command("/diff")
    assert "new.py" in capsys.readouterr().out


def test_permission_aliases_and_full_needs_confirmation(tmp_path, capsys, monkeypatch):
    main, app = make_app(tmp_path)
    app.command("/permissions read-only")
    assert app.settings.permission == "plan" and app.mode.agent.sandbox == sdk.Sandbox.read_only
    monkeypatch.setattr(main.protect, "running_interactively", lambda: False)
    app.command("/permission full")
    assert app.settings.permission == "plan" and "Refused" in capsys.readouterr().out
    monkeypatch.setattr(main.protect, "running_interactively", lambda: True)
    app.screen.ask = lambda question: "yes"
    app.command("/permission danger-full-access")
    assert app.settings.permission == "full" and app.mode.agent.sandbox == sdk.Sandbox.full_access


def test_approval_command(tmp_path, capsys):
    from codex_app import approvals
    main, app = make_app(tmp_path)
    app.command("/approval ask")  # the fake Codex has no request handler to hook into
    assert app.settings.approval == "deny_all" and "available" in capsys.readouterr().out
    app.codex._client = NS(_approval_handler=None)
    app.approver = approvals.Approver()
    app.command("/approval ask")
    assert app.settings.approval == "ask" and app.approver.ask is True
    assert app.codex._client._approval_handler is app.approver
    assert app.mode.agent.approval_mode == approvals.ASK


def test_status_and_new_commands(tmp_path, capsys):
    main, app = make_app(tmp_path, {1: [answer("hi")]})
    app.codex.account = lambda: NS(account=None)
    app.run_task("hello")
    app.command("/status")
    out = capsys.readouterr().out
    assert "thr_1" in out and "not signed in" in out and "Sandbox:" in out
    app.command("/new")
    assert app.mode.is_empty()


def test_main_refuses_system_folders(tmp_path, capsys):
    import main
    assert main.main(["--project", Path(tmp_path).anchor]) == 1
    assert "root of a disk" in capsys.readouterr().out


# --- efficiency -------------------------------------------------------------------------------------

def test_planner_reviews_the_workers_diff(tmp_path):
    diff = "--- a/calc.py\n+++ b/calc.py\n-a - b\n+a + b\n"
    worker_turn = [ev("turn/diff/updated", diff=diff)] + answer("Changed calc.py.")
    codex = FakeCodex({1: [answer("1. Fix add"), answer("VERDICT: APPROVED")], 2: [worker_turn]})
    DualMode(codex, sdk, tmp_path, Settings()).run("fix add")
    review_request = codex.turns[2]["text"]
    assert "THE WORKER'S CHANGES:" in review_request and "+a + b" in review_request


def test_changes_for_review():
    from codex_app.modes import changes_for_review
    assert "no file changes" in changes_for_review("")
    big = "\n".join(f"+line {n}" for n in range(10_000))
    cut = changes_for_review(big, limit=1_000)
    assert "cut short" in cut and len(cut) < 1_300 and "+line 9999" not in cut


def test_update_notice_waits_for_the_next_prompt(tmp_path, capsys, monkeypatch):
    import builtins
    main, app = make_app(tmp_path)
    app.update_notice = NS(take=lambda: "Update available: 1.2.0 -> 1.3.0.")

    def no_more_input(prompt=""):
        raise EOFError
    monkeypatch.setattr(builtins, "input", no_more_input)
    app.loop()
    assert "Update available: 1.2.0 -> 1.3.0." in capsys.readouterr().out


def test_cost_is_unknown_before_anything_is_used():
    assert Usage().cost() is None
