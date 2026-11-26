"""When the planner reviews (planner_review), what it is told about the worker's tests, the review's effort,
and the benchmark's "did the review help?". Fake Codex only: no key, nothing billed."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from bench import run as bench  # noqa: E402
from codex_app import config, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app import modes  # noqa: E402  (not its test_* helpers by name: pytest would collect them)
from codex_app.modes import DualMode, TeamMode, changed_lines  # noqa: E402
from test_app import FakeCodex, answer, ev, item, make_agent  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")


def ran(command, code=0, output="3 passed"):
    return [ev("item/started", item=item(type="commandExecution", command=command)),
            ev("item/completed", item=item(type="commandExecution", command=command, exit_code=code,
                                           aggregated_output=output))]


def diff(lines=3, path="calc.py"):
    body = "".join(f"+line {n}\n" for n in range(lines))
    return [ev("turn/diff/updated", diff=f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}")]


def worker_turn(command="py -m pytest -q", code=0, lines=3, path="calc.py", text="Changed calc.py, tests pass."):
    return (ran(command, code) if command else []) + diff(lines, path) + answer(text)


def verdict(v="APPROVED"):
    return answer(json.dumps({"verdict": v, "summary": "Checked.", "fixes": [] if v == "APPROVED" else ["fix x"]}))


# --- settings -----------------------------------------------------------------------------------------

def test_review_settings_and_effort(monkeypatch):
    assert Settings().planner_review == "auto" and Settings().review_max_lines == 200
    assert Settings(planner_effort="high").review_effort_now() == "medium"
    assert Settings(planner_effort="xhigh").review_effort_now() == "high"
    assert Settings(planner_effort="medium").review_effort_now() == "medium"
    assert Settings(planner_effort="low").review_effort_now() == "low"  # never above the planner's own
    assert Settings(planner_effort="high", review_effort="high").review_effort_now() == "high"
    monkeypatch.setenv("CODEX_APP_PLANNER_REVIEW", "no")
    assert Settings.from_env().planner_review == "never"
    monkeypatch.setenv("CODEX_APP_PLANNER_REVIEW", "yes")
    assert Settings.from_env().planner_review == "always"
    monkeypatch.setenv("CODEX_APP_PLANNER_REVIEW", "sometimes")
    with pytest.raises(ValueError):
        Settings.from_env()
    monkeypatch.delenv("CODEX_APP_PLANNER_REVIEW")
    monkeypatch.setenv("CODEX_APP_REVIEW_EFFORT", "lots")
    with pytest.raises(ValueError):
        Settings.from_env()


# --- what the worker ran ------------------------------------------------------------------------------

def test_agent_keeps_the_last_turns_commands(tmp_path):
    codex = FakeCodex({1: [ran("py -m pytest -q", 1, "\n".join(f"line {n}" for n in range(40))) + answer("a"),
                           answer("b")]})
    agent = make_agent(codex, tmp_path)
    agent.send("x")
    [c] = agent.last_commands
    assert c["command"] == "py -m pytest -q" and c["exit_code"] == 1
    assert c["output"].endswith("line 39") and "line 10" not in c["output"]  # only the end is kept
    agent.send("y")
    assert agent.last_commands == []  # each turn starts afresh


def test_test_commands_and_report():
    commands = [{"command": "git status", "exit_code": 0, "output": ""},
                {"command": "py -m pytest -q", "exit_code": 1, "output": "1 failed"},
                {"command": '"pwsh.exe" -Command "npm test"', "exit_code": 0, "output": "ok"}]
    assert [c["command"] for c in modes.test_runs(commands)] == ["py -m pytest -q", '"pwsh.exe" -Command "npm test"']
    assert modes.tests_passed(commands) and not modes.tests_passed(commands[:2]) and not modes.tests_passed(commands[:1])
    report = modes.test_report(commands)
    assert "$ py -m pytest -q\nexit code 1\n1 failed" in report and "git status" not in report
    assert "no test command" in modes.test_report([])
    assert changed_lines("--- a/x\n+++ b/x\n@@\n+a\n-b\n c\n") == 2


# --- dual mode ----------------------------------------------------------------------------------------

def test_small_change_with_passing_tests_skips_the_review(tmp_path):
    notices = []
    codex = FakeCodex({1: [answer("1. Fix calc.py")], 2: [worker_turn()]})
    mode = DualMode(codex, sdk, tmp_path, Settings(), on_event=lambda a, k, t: notices.append(t))
    assert mode.run("fix add in calc.py") == "Changed calc.py, tests pass."
    assert len([t for t in codex.turns if t["thread"] == "thr_1"]) == 1  # the plan only: no review
    assert mode.review_outcome == "skipped"
    assert any("Planner review skipped: the worker's tests passed (py -m pytest -q) and the change is small "
               "(3 changed lines)" in n for n in notices)


@pytest.mark.parametrize("turn, settings", [
    (worker_turn(code=1), {}),                      # the tests failed
    (worker_turn(command=None), {}),                # no tests were run
    (worker_turn(lines=50), {"review_max_lines": 20}),  # a big change
    (worker_turn(), {"planner_review": "always"}),  # reviews are always on
])
def test_the_planner_reviews_otherwise(tmp_path, turn, settings):
    codex = FakeCodex({1: [answer("plan"), verdict()], 2: [turn]})
    mode = DualMode(codex, sdk, tmp_path, Settings(**settings))
    mode.run("fix add in calc.py")
    assert mode.review_outcome == "approved"
    review = codex.turns[-1]
    assert review["thread"] == "thr_1" and "THE WORKER'S TEST RUNS" in review["text"]
    assert review["options"]["effort"] == "medium"  # one level below the planner's high


def test_the_review_gets_the_workers_test_output(tmp_path):
    codex = FakeCodex({1: [answer("plan"), verdict()], 2: [worker_turn(code=1)]})
    DualMode(codex, sdk, tmp_path, Settings()).run("fix add in calc.py")
    text = codex.turns[-1]["text"]
    assert "$ py -m pytest -q\nexit code 1\n3 passed" in text and "Don't run the same tests again" in \
        codex.started[0]["developer_instructions"]


def test_fix_rounds_report_their_own_tests(tmp_path):
    codex = FakeCodex({1: [answer("plan"), verdict("CHANGES_NEEDED"), verdict()],
                       2: [worker_turn(code=1), worker_turn(command="py -m pytest -q tests/test_calc.py")]})
    mode = DualMode(codex, sdk, tmp_path, Settings())
    mode.run("fix add in calc.py")
    assert mode.review_outcome == "approved"  # once a review started, the fix is reviewed too
    assert "THE WORKER'S TEST RUNS (in this fix)" in codex.turns[-1]["text"]
    assert "tests/test_calc.py" in codex.turns[-1]["text"]


def test_a_worker_that_did_not_finish_is_reviewed(tmp_path):
    failed = ran("py -m pytest -q") + diff() + [
        ev("turn/completed", turn=__import__("types").SimpleNamespace(status="failed", error=None))]
    codex = FakeCodex({1: [answer("plan"), verdict()], 2: [failed]})
    mode = DualMode(codex, sdk, tmp_path, Settings(planner_review="never", retries=0))
    mode.run("fix add in calc.py")
    assert mode.review_outcome == "approved"


def test_never_skips_even_without_tests(tmp_path):
    codex = FakeCodex({1: [answer("plan")], 2: [worker_turn(command=None)]})
    mode = DualMode(codex, sdk, tmp_path, Settings(planner_review="never"))
    mode.run("fix add in calc.py")
    assert mode.review_outcome == "skipped"


def test_web_changes_are_reviewed(tmp_path):
    (tmp_path / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    mode = DualMode(FakeCodex({}), sdk, tmp_path, Settings())
    commands = [{"command": "npm test", "exit_code": 0, "output": "ok"}]
    small = "--- a/index.html\n+++ b/index.html\n@@\n+<p>new</p>\n"
    assert mode.skip_review(commands, small) is None  # the browser check needs the review
    mode.settings.browser_check = False
    assert mode.skip_review(commands, small)


# --- team mode ----------------------------------------------------------------------------------------

def team_plan(*parts):
    return answer(json.dumps({"answer": "Plan.", "parts": [
        {"title": t, "instructions": f"do {t}", "files": f} for t, f in parts]}))


def test_team_with_one_worker_can_skip(tmp_path):
    codex = FakeCodex({1: [team_plan(("calc", ["calc.py"]))], 2: [worker_turn()]})
    mode = TeamMode(codex, sdk, tmp_path, Settings())
    mode.run("fix add in calc.py")
    assert mode.review_outcome == "skipped"


def test_team_with_several_workers_always_reviews(tmp_path):
    notices = []
    codex = FakeCodex({1: [team_plan(("a", ["a.py"]), ("b", ["b.py"])), verdict()],
                       2: [worker_turn(path="a.py")], 3: [worker_turn(path="b.py")]})
    mode = TeamMode(codex, sdk, tmp_path, Settings(planner_review="never"),
                    on_event=lambda a, k, t: notices.append(t))
    mode.run("fix a.py and b.py")
    assert mode.review_outcome == "approved"
    assert any("The planner reviews anyway: several workers' parts were merged" in n for n in notices)
    assert codex.turns[-1]["text"].count("TEST RUNS FOR THIS PART") == 2


# --- --json and the benchmark ---------------------------------------------------------------------------

def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@localhost", **dict(__import__("os").environ)})


def test_json_says_how_the_review_went(tmp_path):
    import main
    root = tmp_path / "p"
    root.mkdir()
    git(root, "init", "-q")
    (root / "calc.py").write_text("x = 1\n", encoding="utf-8")
    codex = FakeCodex({1: [answer("1. plan")], 2: [worker_turn()]})
    app = main.App(codex, sdk, root, Settings(), "dual")
    app.run_task("fix add in calc.py")
    result = app.result()
    assert result["review"] == "skipped" and result["before_review_tree"]
    single = main.App(FakeCodex({1: [answer("done")]}), sdk, root, Settings(), "single")
    single.run_task("fix add in calc.py")
    assert single.result()["review"] is None and single.result()["before_review_tree"] is None


def test_bench_tests_the_project_before_the_review(tmp_path):
    [(name, data, folder)] = bench.load_tasks(["median-bug"])
    base = tmp_path / "run"
    project = bench.make_project(folder, base)
    from codex_app import undo
    unsolved = undo.snapshot(project)
    import shutil
    shutil.copytree(folder / "solution", project, dirs_exist_ok=True)
    solved = undo.snapshot(project)
    assert bench.before_review(folder, project, base, solved) is True
    shutil.rmtree(base / "before-review")
    assert bench.before_review(folder, project, base, unsolved) is False
    assert bench.before_review(folder, project, base, None) is None
    assert (project / "stats.py").read_text(encoding="utf-8") == (folder / "solution" / "stats.py").read_text(
        encoding="utf-8")  # the project itself is left as it was


def test_bench_report_says_whether_the_review_helped():
    def run(mode, passed, before, review):
        return {"task": "a", "mode": mode, "repeat": 1, "passed": passed, "seconds": 10, "input_tokens": 1,
                "output_tokens": 1, "cost_usd": None, "error": None, "hidden_tests": "",
                "passed_before_review": before, "review": review}
    runs = [run("single", True, None, None), run("dual", True, False, "approved"),
            run("dual", True, True, "skipped"), run("dual", False, True, "changes needed")]
    rows = bench.review_rows(runs, ["single", "dual"])
    assert rows == [{"mode": "dual", "runs": 3, "reviewed": 2, "skipped": 1, "passed_before": 2, "passed_after": 2,
                     "rescued": 1, "broken": 1}]
    text = bench.markdown(runs, bench.summarise(runs, ["single", "dual"]), [("a", {}, None)], "now", "1.20.0")
    assert "## Did the review help?" in text and "| dual | 3 | 2 | 1 | 2 | 2 | 1 | 1 |" in text
