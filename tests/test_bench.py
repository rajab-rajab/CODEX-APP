"""Tests for the benchmark (bench/run.py). The tasks are checked for real (each starter fails its hidden tests,
each solution passes); the runs themselves use a fake Codex App, so nothing is billed."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import run as bench  # noqa: E402


@pytest.mark.parametrize("name", [p.name for p in sorted(bench.TASKS.iterdir()) if (p / "task.json").is_file()])
def test_every_task_is_fair(name):
    [(task, data, folder)] = bench.load_tasks([name])
    assert bench.check_task(task, folder) == []
    assert data["title"] and data["kind"] and len(data["prompt"]) > 40


def test_there_are_enough_tasks():
    assert len(bench.load_tasks()) >= 6


def test_unknown_task():
    with pytest.raises(SystemExit, match="No such task: nope"):
        bench.load_tasks(["nope"])


def fake_app(solve: bool, tokens=None, ok=True):
    """Stands in for `main.py --json`: optionally does the task (copies the solution in), prints a result."""
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        project = Path(cmd[cmd.index("--project") + 1])
        if solve:
            shutil.copytree(bench.TASKS / project.name / "solution", project, dirs_exist_ok=True)
        result = {"ok": ok, "answer": "done", "error": None if ok else "it failed", "seconds": 12.5,
                  "files_changed": [{"path": "stats.py", "change": "modified"}] if solve else [],
                  "tokens": tokens or {"m1": {"input": 1000, "cached": 400, "output": 50},
                                       "m2": {"input": 500, "cached": 0, "output": 20}},
                  "cost_usd": 0.0123}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(result) + "\n", "log line\n")
    run.calls = calls
    return run


def test_a_run_that_solves_the_task_passes(tmp_path):
    [(name, data, folder)] = bench.load_tasks(["median-bug"])
    app = fake_app(solve=True)
    result = bench.run_one(name, data, folder, "dual", 1, tmp_path, 60, run=app)
    assert result["passed"] and result["app_ok"] and result["error"] is None
    assert (result["input_tokens"], result["cached_tokens"], result["output_tokens"]) == (1500, 400, 70)
    assert result["cost_usd"] == 0.0123 and result["seconds"] == 12.5 and result["files_changed"] == ["stats.py"]
    cmd, kw = app.calls[0]
    assert cmd[1] == str(bench.APP) and cmd[cmd.index("--mode") + 1] == "dual" and cmd[-1] == data["prompt"]
    assert cmd[cmd.index("--permission") + 1] == "workspace" and cmd[cmd.index("--approval") + 1] == "deny_all"
    env = kw["env"]
    assert env["CODEX_APP_MEMORY"] == "no" and env["CODEX_APP_BROWSER_CHECK"] == "no"
    assert Path(env["CODEX_APP_HOME"]).is_relative_to(tmp_path)  # never your own notes or statistics


def test_hidden_tests_arrive_only_after_codex(tmp_path):
    [(name, data, folder)] = bench.load_tasks(["slugify"])
    seen = []

    def run(cmd, **kw):
        seen.append((Path(cmd[cmd.index("--project") + 1]) / bench.HIDDEN).exists())
        return fake_app(solve=False)(cmd, **kw)
    result = bench.run_one(name, data, folder, "single", 1, tmp_path, 60, run=run)
    # Unsolved, slugify can't even be imported: pytest says "error", and the run fails either way.
    assert seen == [False] and not result["passed"] and result["hidden_tests"]


def test_no_result_from_the_app_is_a_failed_run(tmp_path):
    [(name, data, folder)] = bench.load_tasks(["median-bug"])

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Codex is not signed in.\n")
    result = bench.run_one(name, data, folder, "single", 1, tmp_path, 60, run=broken)
    assert not result["passed"] and "not signed in" in result["error"]


def test_summary_and_report():
    runs = [{"task": "a", "mode": "single", "repeat": 1, "passed": True, "seconds": 10, "input_tokens": 100,
             "output_tokens": 10, "cost_usd": 0.01, "error": None, "hidden_tests": ""},
            {"task": "b", "mode": "single", "repeat": 1, "passed": False, "seconds": 30, "input_tokens": 300,
             "output_tokens": 30, "cost_usd": 0.03, "error": None, "hidden_tests": "1 failed, 2 passed"},
            {"task": "a", "mode": "dual", "repeat": 1, "passed": True, "seconds": 20, "input_tokens": 200,
             "output_tokens": 20, "cost_usd": None, "error": None, "hidden_tests": ""}]
    rows = bench.summarise(runs, ["single", "dual", "team"])
    assert rows == [{"mode": "single", "runs": 2, "passed": 1, "pass_rate": 50, "median_seconds": 20.0,
                     "median_tokens": 220, "cost_usd": 0.04},
                    {"mode": "dual", "runs": 1, "passed": 1, "pass_rate": 100, "median_seconds": 20.0,
                     "median_tokens": 220, "cost_usd": None}]
    tasks = [("a", {"kind": "bug fix"}, None), ("b", {"kind": "feature"}, None)]
    text = bench.markdown(runs, rows, tasks, "2026-11-25 18:00", "1.15.0")
    assert "| single | 1/2 | 50% | 20.0 s | 220 | $0.0400 |" in text
    assert "| dual | 1/1 | 100% | 20.0 s | 220 | – |" in text
    assert "| a | bug fix | 1/1 | 1/1 |" in text and "| b | feature | 0/1 | – |" in text
    assert "- b (single, run 1): 1 failed, 2 passed" in text and "/prices" in text


def test_main_writes_results(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "run_one", lambda name, data, folder, mode, repeat, work, timeout, extra=(): {
        "task": name, "mode": mode, "repeat": repeat, "passed": True, "app_ok": True, "error": None, "seconds": 5,
        "input_tokens": 10, "cached_tokens": 0, "output_tokens": 1, "cost_usd": None, "models": [],
        "files_changed": [], "hidden_tests": ""})
    assert bench.main(["--tasks", "median-bug", "--modes", "single,team", "--out", str(tmp_path)]) == 0
    [md] = tmp_path.glob("*.md")
    [js] = tmp_path.glob("*.json")
    assert "| team | 1/1 | 100% |" in md.read_text(encoding="utf-8")
    assert [r["mode"] for r in json.loads(js.read_text(encoding="utf-8"))["runs"]] == ["single", "team"]


def test_bad_options():
    with pytest.raises(SystemExit):
        bench.main(["--modes", "solo"])
