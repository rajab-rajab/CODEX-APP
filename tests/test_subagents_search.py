"""Tests for conversation search (/history search) and showing Codex's subagents, with a fake Codex."""

import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import engine, sessions  # noqa: E402
from codex_app.agent import _describe_started, describe_subagent_result  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex, answer, ev, item, make_agent  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


# --- searching conversations --------------------------------------------------------------------------

def thread(tid, updated, name=None, preview="", branch=None):
    return NS(id=tid, updated_at=updated, name=name, preview=preview, ephemeral=False, originator="codex_cli_rs",
              source="cli", git_info=NS(branch=branch) if branch else None)


THREADS = [thread("t5", 50, preview="fix the login form", branch="main"),
           thread("t4", 40, preview="add dark mode", branch="feature/dark-mode"),
           thread("t3", 30, name="Login page cleanup", preview="tidy up"),
           thread("t2", 20, preview="update the README"),
           thread("t1-abcdef", 10, preview="first try")]


class SearchCodex(FakeCodex):
    def __init__(self, title_search=True):
        super().__init__({})
        self.title_search, self.listed = title_search, []

    def thread_list(self, cwd=None, limit=None, search_term=None):
        self.listed.append((cwd, limit, search_term))
        if search_term is not None:
            if not self.title_search:
                raise TypeError("unexpected keyword 'search_term'")
            return NS(data=[t for t in THREADS if search_term.lower() in (t.name or "").lower()])
        return NS(data=THREADS[:limit])


def test_search_looks_at_title_first_message_branch_and_id():
    codex = SearchCodex()
    assert [t.id for t in engine.threads(codex, "/p", search="login")] == ["t5", "t3"]  # message and title
    assert [t.id for t in engine.threads(codex, "/p", search="dark-mode")] == ["t4"]  # branch
    assert [t.id for t in engine.threads(codex, "/p", search="abcdef")] == ["t1-abcdef"]  # ID
    assert [t.id for t in engine.threads(codex, "/p", search="login form")] == ["t5"]  # every word
    assert engine.threads(codex, "/p", search="nothing like this") == []
    assert ("/p", 20, "login") in codex.listed and ("/p", engine.SEARCH_SCAN, None) in codex.listed


def test_search_works_without_the_engines_title_search():
    assert [t.id for t in engine.threads(SearchCodex(title_search=False), "/p", search="login")] == ["t5", "t3"]


def test_no_search_lists_the_newest():
    codex = SearchCodex()
    assert [t.id for t in engine.threads(codex, "/p", limit=2)] == ["t5", "t4"] and codex.listed == [("/p", 2, None)]


def make_app(tmp_path, codex):
    import main
    return main.App(codex, sdk, tmp_path, Settings(), "single")


def test_history_search_and_numbers_from_the_search(tmp_path, capsys):
    app = make_app(tmp_path, SearchCodex())
    app.command("/history search dark")
    out = capsys.readouterr().out
    assert "matching 'dark'" in out and " 1. " in out and "add dark mode" in out and "[feature/dark-mode]" in out
    assert "fix the login form" not in out
    app.command("/history 1")  # the first of the search, not the newest conversation
    assert app.mode.agent.thread_id == "t4" and "add dark mode" in capsys.readouterr().out


def test_history_words_without_search_also_search(tmp_path, capsys):
    app = make_app(tmp_path, SearchCodex())
    app.command("/history README")
    assert "update the README" in capsys.readouterr().out
    app.command("/history nothing-like-this")
    assert "No conversation for this project matches" in capsys.readouterr().out


def test_history_number_without_a_list_uses_the_newest(tmp_path):
    app = make_app(tmp_path, SearchCodex())
    app.command("/history 2")
    assert app.mode.agent.thread_id == "t4"


# --- subagents ----------------------------------------------------------------------------------------

class V(Enum):  # the engine's enum values
    spawn = "spawnAgent"
    wait = "wait"
    close = "closeAgent"
    listing = "listAgents"
    send = "sendInput"


def call(tool, status="completed", receivers=(), states=None, model=None, prompt=None):
    return NS(type="collabAgentToolCall", tool=tool, status=NS(value=status), receiver_thread_ids=list(receivers),
              agents_states=states or {}, model=model, prompt=prompt, sender_thread_id="parent")


def test_subagent_calls_are_described():
    assert _describe_started(call(V.spawn, model="gpt-6.2-sol", prompt="Write the tests for calc.py")) == \
        "subagent: start (gpt-6.2-sol): Write the tests for calc.py"
    assert _describe_started(call(V.wait, receivers=["thr_aaaaaa111111", "thr_bbbbbb222222"])) == \
        "subagent: wait for 111111, 222222"
    assert _describe_started(call(V.close, receivers=["x123456"])) == "subagent: close 123456"
    assert _describe_started(call(V.send, receivers=["y999999"])) == "subagent: message 999999"
    assert _describe_started(call(V.listing)) is None


def test_subagent_results_are_described():
    states = {"thr_aaaaaa111111": NS(status=NS(value="completed"), message="done"),
              "thr_bbbbbb222222": NS(status=NS(value="errored"), message="tests failed to run")}
    assert describe_subagent_result(call(V.wait, states=states)) == \
        ("tool_error", "subagents: 1 completed, 1 errored (222222 errored: tests failed to run)")
    good = {"a": NS(status=NS(value="completed"), message=None), "b": NS(status=NS(value="completed"), message=None)}
    assert describe_subagent_result(call(V.wait, states=good)) == ("tool_result", "subagents: 2 completed")
    assert describe_subagent_result(call(V.spawn, status="failed")) == ("tool_error", "subagent start failed")
    assert describe_subagent_result(call(V.spawn)) is None  # the start line already said it
    activity = NS(type="subAgentActivity", kind=NS(value="completed"), agent_path="/root/tester", agent_thread_id="t")
    assert describe_subagent_result(activity) == ("tool_result", "subagent tester completed")
    started = NS(type="subAgentActivity", kind=NS(value="started"), agent_path="/root/shapes", agent_thread_id="t",
                 model="gpt-6.2-sol", reasoning_effort=NS(value="low"))
    assert describe_subagent_result(started) == ("tool", "subagent shapes: started (gpt-6.2-sol, low)")


def activity(kind, path, tid, iid):
    return dict(type="subAgentActivity", kind=NS(value=kind), agent_path=path, agent_thread_id=tid, id=iid,
                model="gpt-6.2-sol" if kind == "started" else None, reasoning_effort=None)


def test_what_the_engine_really_sends(tmp_path, monkeypatch):
    # As engine 0.172 does: subagents start as activities, and Codex waits many times without saying for whom.
    wait = dict(type="collabAgentToolCall", tool=V.wait, status=NS(value="completed"), model=None, prompt=None,
                receiver_thread_ids=[], agents_states={}, sender_thread_id="thr_1", id="w")
    stream = [ev("item/completed", item=item(**activity("started", "/root/shapes", "kid1", "a1"))),
              ev("item/completed", item=item(**activity("started", "/root/words", "kid2", "a2")))]
    stream += [ev("item/started", item=item(**wait)), ev("item/completed", item=item(**wait))] * 3
    stream += [ev("item/completed", item=item(**activity("completed", "/root/shapes", "kid1", "a3"))),
               ev("item/started", item=item(**wait)), ev("item/completed", item=item(**wait)),
               ev("item/completed", item=item(**activity("completed", "/root/words", "kid2", "a4")))]
    looked_at = []
    from codex_app import agent as agent_module
    monkeypatch.setattr(agent_module.engine, "thread_items", lambda codex, tid: looked_at.append(tid) or [])
    events = []
    codex = FakeCodex({1: [stream + answer("Both reviewed.")]})
    agent = make_agent(codex, tmp_path, events)
    agent.secret_guard = lambda item: None
    agent.send("review both")
    lines = [t for k, t in events if k in ("tool", "tool_result", "tool_error")]
    assert lines == ["subagent shapes: started (gpt-6.2-sol)", "subagent words: started (gpt-6.2-sol)",
                     "subagent: wait for subagents", "subagent shapes completed", "subagent: wait for subagents",
                     "subagent words completed"]  # the repeated waits are shown once each time
    assert agent.subagents == {"kid1", "kid2"} and looked_at == ["kid1", "kid2"]


def test_a_subagent_touching_a_secret_is_reported(tmp_path, monkeypatch):
    from codex_app import agent as agent_module, protect
    secret_read = NS(type="commandExecution", command="pwsh -Command 'Get-Content .env'", command_actions=[])
    monkeypatch.setattr(agent_module.engine, "thread_items", lambda codex, tid: [secret_read])
    events = []
    codex = FakeCodex({1: [[ev("item/completed", item=item(**activity("started", "/root/x", "kid", "a")))]
                           + answer("done")]})
    agent = make_agent(codex, tmp_path, events)
    agent.secret_guard = protect.guard_item
    agent.send("go")
    errors = [t for k, t in events if k == "tool_error"]
    assert len(errors) == 1 and "a subagent" in errors[0] and ".env" in errors[0] and "reached OpenAI" in errors[0]
    assert agent.guard_hits and "a subagent" in agent.guard_hits[0]


def test_a_subagent_that_cant_be_read_is_said(tmp_path, monkeypatch):
    from codex_app import agent as agent_module

    def broken(codex, tid):
        raise RuntimeError("no such thread")
    monkeypatch.setattr(agent_module.engine, "thread_items", broken)
    events = []
    codex = FakeCodex({1: [[ev("item/completed", item=item(**activity("started", "/root/x", "kid123456", "a")))]
                           + answer("done")]})
    agent = make_agent(codex, tmp_path, events)
    agent.secret_guard = lambda item: None
    agent.send("go")
    assert ("notice", "Couldn't check what subagent 123456 did for secret files.") in events


def test_subagents_show_up_while_codex_works(tmp_path):
    spawn = dict(type="collabAgentToolCall", tool=V.spawn, status=NS(value="completed"), model=None,
                 prompt="check the docs", receiver_thread_ids=["thr_child_123456"], agents_states={},
                 sender_thread_id="thr_1")
    wait = dict(spawn, tool=V.wait, prompt=None,
                agents_states={"thr_child_123456": NS(status=NS(value="completed"), message="all good")})
    events = []
    codex = FakeCodex({1: [[ev("item/started", item=item(**spawn)), ev("item/completed", item=item(**spawn)),
                            ev("item/started", item=item(**wait)), ev("item/completed", item=item(**wait))]
                           + answer("Done, with a subagent.")]})
    agent = make_agent(codex, tmp_path, events)
    agent.send("do it")
    assert ("tool", "subagent: start: check the docs") in events
    assert ("tool", "subagent: wait for 123456") in events and ("tool_result", "subagents: 1 completed") in events
    assert agent.subagents == {"thr_child_123456"}


def test_json_result_counts_subagents(tmp_path):
    app = make_app(tmp_path, FakeCodex({1: [answer("ok")]}))
    app.run_task("x")
    app.mode.agent.subagents.update({"a", "b"})
    assert app.result()["subagents"] == 2
