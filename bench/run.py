"""The benchmark: how often single, dual and team mode get fixed tasks right, and what each costs.

    py bench/run.py                                  every task in every mode, once
    py bench/run.py --modes single,dual --repeat 3   some modes, several times (models vary between runs)
    py bench/run.py --tasks median-bug,slugify       some tasks
    py bench/run.py --check                          only check the tasks themselves (no model, nothing billed)

Each task in bench/tasks/<name>/ has:
    task.json   its title, kind and the prompt Codex gets
    start/      the project as Codex finds it
    hidden/     tests Codex never sees: they are copied in only after Codex has finished
    solution/   a correct answer (files laid over start/), for --check

For every task and mode, the runner copies start/ into a fresh temporary git project, runs Codex App on it
exactly as a user would (`main.py --json`, permission workspace, approval deny_all), then copies the hidden
tests in and runs them with pytest. A run passes only if every hidden test passes. Tokens, cost (when
/prices has prices for the models), time and the files changed come from the app's own --json result.

The runs use your Codex sign-in (or OPENAI_API_KEY) and are billed like any task. Memory, the browser
check, the update check and the finish signal are off, and app data (sessions, usage log) goes to a
temporary folder, so a run never changes your own notes or statistics. Results are written to
bench/results/<date-time>.json and .md (the folder is in .gitignore).

`--check` makes sure every task is fair: the starter project fails the hidden tests and the solution
passes them. The normal test run (tests/test_bench.py) does this, so a broken task never gets in."""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
TASKS = BENCH / "tasks"
APP = BENCH.parent / "main.py"
RESULTS = BENCH / "results"
MODES = ("single", "dual", "team")
HIDDEN = "bench_hidden"  # where the hidden tests go in the project, after Codex is done


def load_tasks(names=None) -> list:
    """[(name, task.json data, folder)], sorted by name."""
    found = []
    for folder in sorted(p for p in TASKS.iterdir() if (p / "task.json").is_file()):
        if names and folder.name not in names:
            continue
        found.append((folder.name, json.loads((folder / "task.json").read_text(encoding="utf-8")), folder))
    unknown = set(names or ()) - {name for name, _, _ in found}
    if unknown:
        raise SystemExit(f"No such task: {', '.join(sorted(unknown))}. Tasks: "
                         + ", ".join(p.name for p in sorted(TASKS.iterdir()) if p.is_dir()))
    return found


def _git(project: Path, *args) -> None:
    subprocess.run(["git", *args], cwd=project, capture_output=True, check=True, stdin=subprocess.DEVNULL,
                   env={**os.environ, "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@localhost",
                        "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@localhost"})


def make_project(task_folder: Path, into: Path, with_solution: bool = False, git: bool = True) -> Path:
    """A fresh git project with the task's starting files (and the solution laid over them). git=False for
    --check, which needs no history (and git is slow to start on Windows)."""
    project = into / task_folder.name
    shutil.copytree(task_folder / "start", project)
    if with_solution:
        shutil.copytree(task_folder / "solution", project, dirs_exist_ok=True)
    if not git:
        return project
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "start")
    return project


def hidden_tests(task_folder: Path, project: Path, timeout: int = 300) -> tuple:
    """Copy the hidden tests in and run them. (passed, the end of pytest's output)."""
    shutil.copytree(task_folder / "hidden", project / HIDDEN, dirs_exist_ok=True)
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", HIDDEN],
                              cwd=project, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "the hidden tests took too long"
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
    return done.returncode == 0, tail


def check_task(name: str, folder: Path) -> list:
    """What is wrong with a task ([] if nothing): the start must fail its hidden tests, the solution pass."""
    problems = []
    for part in ("start", "hidden", "solution"):
        if not (folder / part).is_dir():
            problems.append(f"{name}: no {part}/ folder")
    data = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    if not str(data.get("prompt", "")).strip():
        problems.append(f"{name}: task.json has no prompt")
    if problems:
        return problems
    # On Windows git can hold its temporary files open for a moment: a leftover there is harmless.
    with tempfile.TemporaryDirectory(prefix="codex-bench-check-", ignore_cleanup_errors=True) as tmp:
        passed, out = hidden_tests(folder, make_project(folder, Path(tmp) / "start", git=False))
        if passed:
            problems.append(f"{name}: the starting project already passes the hidden tests")
        passed, out = hidden_tests(folder, make_project(folder, Path(tmp) / "solution", with_solution=True, git=False))
        if not passed:
            problems.append(f"{name}: the solution fails the hidden tests:\n{out}")
    return problems


def app_env(home: Path) -> dict:
    """The environment for a benchmark run: app data in a temporary folder, nothing that skews results."""
    env = {**os.environ, "CODEX_APP_HOME": str(home), "CODEX_APP_UPDATE_CHECK": "no", "CODEX_APP_NOTIFY": "no",
           "CODEX_APP_MEMORY": "no", "CODEX_APP_BROWSER_CHECK": "no", "CODEX_APP_BROWSER": "no",
           "PYTHONUNBUFFERED": "1"}
    prices = Path(os.environ.get("CODEX_APP_HOME", Path.home() / ".codex-app")) / "prices.json"
    if prices.is_file():  # your /prices, so costs show in dollars
        home.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prices, home / "prices.json")
    return env


def run_one(name: str, data: dict, folder: Path, mode: str, repeat: int, work: Path, timeout: int,
            extra_args=(), run=subprocess.run) -> dict:
    """One task in one mode: Codex App works on a fresh copy, then the hidden tests decide."""
    base = work / f"{name}-{mode}-{repeat}"
    project = make_project(folder, base)
    started = time.monotonic()
    result, error = {}, None
    try:
        done = run([sys.executable, str(APP), "--project", str(project), "--mode", mode, "--json",
                    "--permission", "workspace", "--approval", "deny_all", *extra_args, "--", data["prompt"]],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                   env=app_env(base / "home"), stdin=subprocess.DEVNULL)
        try:
            result = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            error = "no result from the app: " + (done.stderr.strip().splitlines() or ["?"])[-1][:300]
        (base / "app-log.txt").write_text(done.stderr, encoding="utf-8")
    except subprocess.TimeoutExpired:
        error = f"stopped after {timeout} s"
    seconds = round(time.monotonic() - started, 1)
    passed, tests = hidden_tests(folder, project)
    tokens = result.get("tokens") or {}
    return {"task": name, "mode": mode, "repeat": repeat, "passed": passed, "app_ok": bool(result.get("ok")),
            "error": error or result.get("error"), "seconds": result.get("seconds") or seconds,
            "input_tokens": sum(t.get("input", 0) for t in tokens.values()),
            "cached_tokens": sum(t.get("cached", 0) for t in tokens.values()),
            "output_tokens": sum(t.get("output", 0) for t in tokens.values()),
            "cost_usd": result.get("cost_usd"), "models": sorted(tokens),
            "files_changed": [f["path"] for f in result.get("files_changed") or []],
            "hidden_tests": tests if not passed else ""}


def summarise(runs: list, modes) -> list:
    """One row per mode: runs, passed, pass rate, median seconds and tokens, total cost."""
    rows = []
    for mode in modes:
        mine = [r for r in runs if r["mode"] == mode]
        if not mine:
            continue
        costs = [r["cost_usd"] for r in mine]
        rows.append({"mode": mode, "runs": len(mine), "passed": sum(r["passed"] for r in mine),
                     "pass_rate": round(100 * sum(r["passed"] for r in mine) / len(mine)),
                     "median_seconds": round(statistics.median(r["seconds"] for r in mine), 1),
                     "median_tokens": int(statistics.median(r["input_tokens"] + r["output_tokens"] for r in mine)),
                     "cost_usd": None if None in costs else round(sum(costs), 4)})
    return rows


def markdown(runs: list, rows: list, tasks: list, when: str, app_version: str) -> str:
    """The results as a Markdown report."""
    def money(c):
        return "–" if c is None else f"${c:.4f}"
    out = [f"# Codex App benchmark, {when}", "", f"App {app_version}. {len(tasks)} task(s); a run passes only if "
           "every hidden test passes.", "", "| Mode | Passed | Pass rate | Median time | Median tokens | Cost |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['mode']} | {r['passed']}/{r['runs']} | {r['pass_rate']}% | {r['median_seconds']:.1f} s | "
                   f"{r['median_tokens']:,} | {money(r['cost_usd'])} |")
    out += ["", "| Task | Kind | " + " | ".join(r["mode"] for r in rows) + " |",
            "|---|---|" + "---|" * len(rows)]
    for name, data, _ in tasks:
        cells = []
        for r in rows:
            mine = [x for x in runs if x["task"] == name and x["mode"] == r["mode"]]
            cells.append(f"{sum(x['passed'] for x in mine)}/{len(mine)}" if mine else "–")
        out.append(f"| {name} | {data.get('kind', '')} | " + " | ".join(cells) + " |")
    failed = [r for r in runs if not r["passed"]]
    if failed:
        out += ["", "## Failed runs", ""]
        for r in failed:
            why = r["error"] or (r["hidden_tests"].splitlines() or ["hidden tests failed"])[-1]
            out.append(f"- {r['task']} ({r['mode']}, run {r['repeat']}): {why}")
    if any(r["cost_usd"] is None for r in rows):
        out += ["", "Costs show once the models have prices (`/prices <model> <in> <cached> <out>` in the app)."]
    return "\n".join(out) + "\n"


def app_version() -> str:
    text = (BENCH.parent / "codex_app" / "__init__.py").read_text(encoding="utf-8")
    return next((line.split('"')[1] for line in text.splitlines() if line.startswith("__version__")), "?")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Codex App's benchmark: single, dual and team mode on fixed tasks.")
    parser.add_argument("--tasks", help="comma-separated task names (default: all)")
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated: single,dual,team (default: all)")
    parser.add_argument("--repeat", type=int, default=1, help="runs per task and mode (default 1)")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per run before it is stopped")
    parser.add_argument("--power", choices=("normal", "high", "max"), help="the app's power level for every run")
    parser.add_argument("--check", action="store_true", help="only check the tasks (no model, nothing billed)")
    parser.add_argument("--keep", action="store_true", help="keep the projects Codex worked on (and their logs)")
    parser.add_argument("--out", default=str(RESULTS), help="where results go (default bench/results)")
    args = parser.parse_args(argv)
    tasks = load_tasks([t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks else None)

    if args.check:
        problems = [p for name, _, folder in tasks for p in check_task(name, folder)]
        for name, data, _ in tasks:
            print(f"  {'FAIL' if any(p.startswith(name + ':') for p in problems) else 'ok  '}  {name}: {data['title']}")
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if any(m not in MODES for m in modes) or args.repeat < 1:
        parser.error("--modes takes single, dual and team; --repeat 1 or more")
    extra = ["--power", args.power] if args.power else []
    total = len(tasks) * len(modes) * args.repeat
    print(f"{total} run(s): {len(tasks)} task(s) x {', '.join(modes)} x {args.repeat}. They are billed like any task.")
    work = Path(tempfile.mkdtemp(prefix="codex-bench-"))
    out, stamp, when = Path(args.out), time.strftime("%Y%m%d-%H%M%S"), time.strftime("%Y-%m-%d %H:%M")
    print(f"Results are saved after every run: {out / (stamp + '.md')} and .json")
    runs = []
    try:
        for repeat in range(1, args.repeat + 1):
            for name, data, folder in tasks:
                for mode in modes:
                    print(f"[{len(runs) + 1}/{total}] {name}, {mode} ...", flush=True)
                    run = run_one(name, data, folder, mode, repeat, work, args.timeout, extra)
                    runs.append(run)
                    print(f"    {'PASSED' if run['passed'] else 'failed'} in {run['seconds']} s, "
                          f"{run['input_tokens'] + run['output_tokens']:,} tokens"
                          + (f" ({run['error']})" if run["error"] else ""), flush=True)
                    save_results(out, stamp, when, runs, tasks, modes, total)  # a stopped run keeps what it did
    except KeyboardInterrupt:
        print(f"\nStopped after {len(runs)} of {total} run(s).")
    finally:
        if args.keep:
            print(f"Projects and logs kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    report = save_results(out, stamp, when, runs, tasks, modes, total)
    print("\n" + report + f"\nSaved: {out / (stamp + '.md')} and .json")
    return 0 if len(runs) == total else 1


def save_results(out: Path, stamp: str, when: str, runs: list, tasks: list, modes, total: int) -> str:
    """Write the report and the JSON for the runs done so far (each file replaced whole, so a stopped
    benchmark never leaves half a file). Returns the report."""
    rows = summarise(runs, modes)
    report = markdown(runs, rows, tasks, when, app_version())
    if len(runs) < total:
        report = report.replace("\n\n", f"\n\n**Partial: {len(runs)} of {total} planned runs.** The benchmark "
                                       "was stopped, or is still running.\n\n", 1)
    out.mkdir(parents=True, exist_ok=True)
    data = {"when": when, "app_version": app_version(), "planned_runs": total, "complete": len(runs) == total,
            "summary": rows, "runs": runs}
    for suffix, text in ((".json", json.dumps(data, indent=2)), (".md", report)):
        path = out / f"{stamp}{suffix}"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return report


if __name__ == "__main__":
    sys.exit(main())
