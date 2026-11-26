"""Tokens per agent and per step (plan, work, review, fix, ...) in --json and the benchmark. Fake Codex only."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from bench import run as bench  # noqa: E402
from codex_app import config, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import DualMode  # noqa: E402
from test_app import FakeCodex, answer, ev, item, make_agent  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")
    saved = dict(config.PRICES)
    config.PRICES.clear()
    yield
    config.PRICES.clear()
    config.PRICES.update(saved)


def tokens(step):
    return (step["input"], step["cached"], step["output"])


def test_each_turn_records_what_it_used(tmp_path):
    command = [ev("item/started", item=item(type="commandExecution", command="py -m pytest -q"))]
    codex = FakeCodex({1: [command + answer("one", usage=(100, 20, 30)), answer("two", usage=(250, 50, 70))]})
    agent = make_agent(codex, tmp_path)
    agent.step = "plan"
    agent.send("a")
    agent.step = "review"
    agent.send("b")
    first, second = agent.steps
    assert (first["agent"], first["step"], first["model"]) == ("codex", "plan", "gpt-5.3-codex")
    assert tokens(first) == (100, 20, 30) and first["reasoning"] == 5 and first["tool_calls"] == 1
    assert second["step"] == "review" and tokens(second) == (150, 30, 40) and second["tool_calls"] == 0


def test_a_new_thread_counts_from_zero(tmp_path):
    codex = FakeCodex({1: [answer("one", usage=(500, 0, 50))], 2: [answer("two", usage=(80, 0, 8))]})
    agent = make_agent(codex, tmp_path)
    agent.send("a")
    agent.reset()  # Codex's total for the new thread starts again from 0
    agent.send("b")
    assert tokens(agent.steps[1]) == (80, 0, 8)
    assert agent.steps[1]["step"] == "turn"  # no step set: an ordinary turn


def test_dual_mode_names_its_steps(tmp_path):
    codex = FakeCodex({1: [answer("plan", usage=(100, 0, 10)),
                           answer("VERDICT: CHANGES_NEEDED\n- add a test", usage=(300, 50, 30)),
                           answer("VERDICT: APPROVED", usage=(600, 150, 40))],
                       2: [answer("did it", usage=(200, 0, 20)), answer("added test", usage=(500, 100, 60))]})
    mode = DualMode(codex, sdk, tmp_path, Settings())
    mode.run("task")
    assert [s["step"] for s in mode.planner.steps] == ["plan", "review", "review"]
    assert [s["step"] for s in mode.worker.steps] == ["work", "fix"]
    assert [tokens(s) for s in mode.worker.steps] == [(200, 0, 20), (300, 100, 40)]


def make_app(root, script, mode="single", **settings):
    import main
    return main.App(FakeCodex(script), sdk, root, Settings(**settings), mode)


def test_json_result_has_steps_and_agents(tmp_path):
    config.PRICES["gpt-6.2-astra"] = (2.0, 0.5, 10.0)
    app = make_app(tmp_path, {1: [answer("1. plan", usage=(1000, 0, 100)),
                                  answer("VERDICT: APPROVED", usage=(3000, 1000, 150))],
                              2: [answer("done", usage=(2000, 500, 200))]}, mode="dual")
    app.run_task("fix the failing test in calc.py")
    result = app.result()
    json.dumps(result)  # plain JSON
    assert [(s["agent"], s["step"]) for s in result["steps"]] == [("planner", "plan"), ("worker", "work"),
                                                                  ("planner", "review")]
    plan = result["steps"][0]
    assert tokens(plan) == (1000, 0, 100) and plan["cost_usd"] == round((1000 * 2 + 100 * 10) / 1e6, 6)
    assert "cost_usd" not in result["steps"][1]  # the worker's model has no price
    assert "at" not in plan
    planner = result["agents"]["planner"]
    assert planner["turns"] == 2 and (planner["input"], planner["cached"], planner["output"]) == (3000, 1000, 150)
    assert result["agents"]["worker"]["model"] == Settings().worker_model


def test_next_task_reports_only_its_own_steps(tmp_path):
    app = make_app(tmp_path, {1: [answer("one", usage=(100, 0, 10)), answer("two", usage=(300, 0, 30))]})
    app.run_task("fix the failing test in calc.py")
    app.run_task("add a test for subtract in calc.py")
    assert [(s["step"], tokens(s)) for s in app.result()["steps"]] == [("task", (200, 0, 20))]


def test_helper_threads_count_in_the_totals(tmp_path):
    """A browser check runs in a thread of its own, outside the mode's agents: its tokens count too."""
    app = make_app(tmp_path, {1: [answer("done", usage=(100, 0, 10))], 2: [answer("ok", usage=(40, 0, 4))]})
    helper = make_agent(app.codex, tmp_path)
    helper.name, helper.step = "browser-check", "browser check"
    original = app.mode.agent.send

    def send(message, **kw):
        out = original(message, **kw)
        app.mode.helpers.append(helper)
        helper.send("look")
        return out
    app.mode.agent.send = send
    app.run_task("fix the failing test in calc.py")
    result = app.result()
    assert [s["step"] for s in result["steps"]] == ["task", "browser check"]
    assert sum(t["input"] for t in result["tokens"].values()) == 140


def test_bench_sums_steps_and_reports_them():
    steps = [{"step": "plan", "input": 1000, "cached": 0, "output": 100, "tool_calls": 3, "seconds": 5.0},
             {"step": "work", "input": 2000, "cached": 1000, "output": 200, "tool_calls": 8, "seconds": 9.0},
             {"step": "review", "input": 500, "cached": 0, "output": 50, "tool_calls": 1, "seconds": 2.0},
             {"step": "review", "input": 400, "cached": 100, "output": 50, "tool_calls": 1, "seconds": 1.5}]
    per_run = bench.by_step(steps)
    assert per_run["review"] == {"turns": 2, "input": 900, "cached": 100, "output": 100, "tool_calls": 2,
                                 "seconds": 3.5}
    runs = [{"task": "a", "mode": "dual", "repeat": n, "passed": True, "seconds": 10, "input_tokens": 3900,
             "output_tokens": 400, "cost_usd": None, "error": None, "hidden_tests": "", "by_step": per_run}
            for n in (1, 2)]
    rows = bench.step_rows(runs, ["single", "dual"])
    work = next(r for r in rows if r["step"] == "work")
    assert work == {"mode": "dual", "step": "work", "runs": 2, "turns": 1.0, "tool_calls": 8.0, "tokens": 2200,
                    "uncached_input": 1000, "output": 200, "seconds": 9.0, "share": 51}
    text = bench.markdown(runs, bench.summarise(runs, ["dual"]), [("a", {"kind": "bug fix"}, None)],
                          "2026-11-26 12:00", "1.19.0")
    assert "## Where the tokens went" in text and "| dual | work | 1.0 | 8.0 | 2,200 | 51% | 1,000 | 200 | 9.0 s |" in text


def test_bench_report_without_step_data_has_no_step_table():
    runs = [{"task": "a", "mode": "single", "repeat": 1, "passed": True, "seconds": 10, "input_tokens": 100,
             "output_tokens": 10, "cost_usd": None, "error": None, "hidden_tests": ""}]
    text = bench.markdown(runs, bench.summarise(runs, ["single"]), [("a", {}, None)], "now", "1.19.0")
    assert "Where the tokens went" not in text


def test_two_helpers_with_the_same_name_both_count(tmp_path):
    from codex_app import costs
    codex = FakeCodex({1: [answer("a", usage=(40, 0, 4))], 2: [answer("b", usage=(60, 0, 6))]})
    first, second = make_agent(codex, tmp_path), make_agent(codex, tmp_path)
    first.send("x")
    second.send("y")
    assert costs.record({}, [first, second], tmp_path, "dual")["gpt-5.3-codex"] == [100, 0, 10]
