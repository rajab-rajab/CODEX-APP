"""Tests for scheduled tasks (/schedule, --run-schedule). A fake scheduler stands in for Task Scheduler and
cron, so nothing is added to this computer; set CODEX_APP_TEST_REAL_SCHEDULER=yes (GitHub Actions does) to
also add and remove one real entry."""

import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import config, schedule, sessions, undo  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex  # noqa: E402


class FakeScheduler:
    def __init__(self):
        self.added, self.removed = {}, []

    def add(self, s):
        self.added[s.id] = s

    def remove(self, schedule_id):
        self.removed.append(schedule_id)
        self.added.pop(schedule_id, None)

    def exists(self, schedule_id):
        return schedule_id in self.added


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    fake = FakeScheduler()
    monkeypatch.setattr(schedule, "SCHEDULER", fake)
    return fake


def make(task="run the tests", project="C:/p", when=("daily", "09:00"), **settings):
    timing, _ = schedule.parse_when(list(when))
    return schedule.Schedule(id=schedule.new_id(), task=task, project=str(project), when=vars(timing),
                             settings={"permission": "workspace", "approval": "deny_all", "power": "normal",
                                       "profile": "auto", **settings})


# --- when ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("words,described,rest", [
    ("daily 9:05 fix it", "every day at 09:05", ["fix", "it"]),
    ("weekdays 18:00 x", "weekdays at 18:00", ["x"]),
    ("weekends 10:30 x", "weekends at 10:30", ["x"]),
    ("fri,mon 08:00 x", "Mon, Fri at 08:00", ["x"]),
    ("Tuesday 07:45 x", "Tue at 07:45", ["x"]),
    ("hourly x y", "every hour", ["x", "y"]),
    ("every 3 hours x", "every 3 hours", ["x"]),
])
def test_parse_when(words, described, rest):
    when, left = schedule.parse_when(words.split())
    assert when.describe() == described and left == rest


@pytest.mark.parametrize("words,message", [
    ("daily fix it", "needs a time"),
    ("daily 25:00 x", "needs a time"),
    ("every 30 hours x", "1 to 23"),
    ("sometimes 09:00 x", "Write when"),
    ("", "Write when"),
])
def test_parse_when_refuses(words, message):
    with pytest.raises(ValueError, match=message):
        schedule.parse_when(words.split())


# --- the computer's scheduler -------------------------------------------------------------------------

def _xml(text):
    return ET.fromstring(text.split("\n", 1)[1])  # without the UTF-16 declaration (the file is written as UTF-16)


NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_windows_task_xml():
    s = make(project="C:/My Projects/site", when=("mon,wed", "08:30"))
    cmd = [r"C:\Program Files\Python312\pythonw.exe", r"C:\Users\A B\codex app\main.py", "--run-schedule", s.id]
    root = _xml(schedule.windows_xml(s, cmd))
    assert root.find(".//t:Exec/t:Command", NS).text == cmd[0]
    assert root.find(".//t:Exec/t:Arguments", NS).text == f'"{cmd[1]}" --run-schedule {s.id}'
    assert root.find(".//t:Exec/t:WorkingDirectory", NS).text == "C:/My Projects/site"
    assert root.find(".//t:CalendarTrigger/t:StartBoundary", NS).text.endswith("T08:30:00")
    days = [d.tag.split("}")[1] for d in root.find(".//t:DaysOfWeek", NS)]
    assert days == ["Monday", "Wednesday"]
    settings = root.find("t:Settings", NS)
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert root.find(".//t:LogonType", NS).text == "InteractiveToken"  # no stored password


def test_windows_hourly_and_daily_triggers():
    hourly = _xml(schedule.windows_xml(make(when=("every", "2", "hours")), ["py", "main.py"]))
    assert hourly.find(".//t:Repetition/t:Interval", NS).text == "PT2H"
    daily = _xml(schedule.windows_xml(make(), ["py", "main.py"]))
    assert daily.find(".//t:ScheduleByDay/t:DaysInterval", NS).text == "1"


def test_task_text_is_escaped_in_the_xml():
    s = make(task='fix <b> & "quotes"')
    assert _xml(schedule.windows_xml(s, ["py", "main.py"])).find(".//t:Description", NS).text.endswith(
        'fix <b> & "quotes"')


def test_cron_lines():
    cmd = ["/usr/bin/python3", "/home/a b/main.py", "--run-schedule", "abc"]
    s = make(when=("weekdays", "18:05"))
    s.id = "abc"
    assert schedule.cron_line(s, cmd) == ("5 18 * * 1,2,3,4,5 /usr/bin/python3 '/home/a b/main.py' --run-schedule abc"
                                          " >/dev/null 2>&1 # codex-app:abc")
    assert schedule.cron_line(make(when=("every", "4", "hours")), cmd).startswith("0 */4 * * * ")
    assert schedule.cron_line(make(when=("sun", "07:00")), cmd).startswith("0 7 * * 0 ")


# --- saved schedules ----------------------------------------------------------------------------------

def test_settings_for_never_full_never_ask():
    with pytest.raises(ValueError, match="full"):
        schedule.settings_for(Settings(permission="full"))
    kept = schedule.settings_for(Settings(approval="ask", power="high"))
    assert kept["approval"] == "deny_all" and kept["power"] == "high" and kept["permission"] == "workspace"


def test_add_and_remove(home):
    s = schedule.add(make())
    assert home.added == {s.id: s} and [x.id for x in schedule.load_all()] == [s.id]
    assert schedule.get(s.id).timing.describe() == "every day at 09:00"
    assert schedule.remove(s.id) and schedule.load_all() == [] and home.removed == [s.id]
    assert not schedule.remove(s.id)


def test_argv_for_a_run_is_accepted_by_main(tmp_path):
    import main
    s = make(project=tmp_path, mode="dual", single_model="m1", planner_model="m2", worker_model="m3", team_size=2,
             web_search=True)
    s.mode = "dual"
    argv = schedule.argv_for(s)
    assert argv[-2:] == ["--", "run the tests"] and argv[argv.index("--mode") + 1] == "dual"
    assert "--search" in argv and argv[argv.index("--planner-model") + 1] == "m2"
    # the same parser main() uses; --selftest --offline stops right after parsing, without the engine
    assert main.main(["--selftest", "--offline", "--quiet", *argv]) == 0


# --- runs ---------------------------------------------------------------------------------------------

def fake_main(result, log="working...\n"):
    calls = []

    def run(argv):
        calls.append((argv, dict(os.environ)))
        print(log, file=sys.stderr, end="")
        print("\033[31mcoloured\033[0m", file=sys.stderr)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    run.calls = calls
    return run


def test_a_run_keeps_its_result_and_log(tmp_path, monkeypatch):
    import main
    s = schedule.add(make(project=tmp_path))
    monkeypatch.setattr(main.prefs, "signal_done", lambda text: None)
    run = fake_main({"ok": True, "answer": "All green.", "files_changed": [], "before_tree": "abc"})
    assert main.run_scheduled(s.id, run_main=run) == 0
    argv, env = run.calls[0]
    assert argv == schedule.argv_for(s) and env["CODEX_APP_UPDATE_CHECK"] == "no" and env["CODEX_APP_BROWSER"] == "no"
    assert "CODEX_APP_SCHEDULED" not in os.environ  # put back after the run
    [(path, report)] = schedule.runs(s.id)
    assert report["result"]["answer"] == "All green." and report["exit_code"] == 0
    assert "working..." in report["log"] and "\033[" not in report["log"]


def test_a_crash_is_kept_too(tmp_path, monkeypatch):
    import main
    s = schedule.add(make(project=tmp_path))
    monkeypatch.setattr(main.prefs, "signal_done", lambda text: None)

    def crash(argv):
        raise RuntimeError("engine gone")
    assert main.run_scheduled(s.id, run_main=crash) == 1
    report = schedule.runs(s.id)[0][1]
    assert report["error"] == "RuntimeError: engine gone" and "Traceback" in report["log"]
    assert sys.stdout is not None and not isinstance(sys.stdout, type(None))


def test_one_run_at_a_time(tmp_path, monkeypatch):
    import main
    s = schedule.add(make(project=tmp_path))
    monkeypatch.setattr(main.prefs, "signal_done", lambda text: None)
    lock = config.APP_HOME / "schedules" / f"{s.id}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("123")
    run = fake_main({"ok": True})
    main.run_scheduled(s.id, run_main=run)
    assert run.calls == [] and schedule.runs(s.id)[0][1]["skipped"]
    old = time.time() - schedule.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))  # left by a run that died
    main.run_scheduled(s.id, run_main=run)
    assert len(run.calls) == 1 and not lock.exists()


@pytest.mark.skipif(os.name != "nt", reason="windows only")
def test_programs_a_scheduled_run_starts_get_no_window(monkeypatch):
    seen = []
    original = subprocess.Popen.__init__

    def record(popen, *args, **kwargs):
        seen.append(kwargs.get("creationflags", 0))
        raise OSError("not really started")
    monkeypatch.setattr(subprocess.Popen, "__init__", record)
    with schedule.HiddenWindows():
        for flags in (None, subprocess.CREATE_NEW_CONSOLE):
            with pytest.raises(OSError):
                subprocess.Popen(["x"], **({} if flags is None else {"creationflags": flags}))
    assert seen == [subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NEW_CONSOLE]  # an asked-for window stays
    assert subprocess.Popen.__init__ is record and original is not record  # put back after the run


def test_unknown_schedule_does_nothing():
    import main
    assert main.run_scheduled("nope", run_main=lambda argv: pytest.fail("ran")) == 2


def test_old_runs_are_dropped(monkeypatch):
    monkeypatch.setattr(schedule, "RUNS_KEPT", 3)
    folder = schedule.runs_dir("x")
    folder.mkdir(parents=True)
    for n in range(5):
        (folder / f"2026010{n}-000000.json").write_text("{}")
    schedule.save_run("x", {"ok": True}, "")
    assert len(list(folder.glob("*.json"))) == 3


# --- the /schedule command ----------------------------------------------------------------------------

def make_app(root, answer="y", **settings):
    import main
    app = main.App(FakeCodex({}), sdk, root, Settings(**settings), "single")
    app.ask = lambda q: answer
    return app


def test_schedule_command_creates_after_a_yes(tmp_path, capsys, home):
    app = make_app(tmp_path, power="high")
    app.command("/schedule weekdays 09:00 run the tests and fix what fails")
    out = capsys.readouterr().out
    [s] = schedule.load_all()
    assert "weekdays at 09:00" in out and "Created schedule" in out and s.id in home.added
    assert s.task == "run the tests and fix what fails" and s.project == str(tmp_path) and s.mode == "single"
    assert s.settings["power"] == "high"
    app.command("/schedule")
    listed = capsys.readouterr().out
    assert s.id in listed and "never run" in listed and "missing" not in listed
    app.command(f"/schedule remove {s.id}")
    assert schedule.load_all() == [] and "Removed" in capsys.readouterr().out


def test_schedule_command_needs_a_yes_and_refuses_full(tmp_path, capsys):
    make_app(tmp_path, answer="n").command("/schedule daily 09:00 x")
    assert schedule.load_all() == [] and "Not created" in capsys.readouterr().out
    make_app(tmp_path, permission="full").command("/schedule daily 09:00 x")
    assert schedule.load_all() == [] and "can't use 'full'" in capsys.readouterr().out
    make_app(tmp_path).command("/schedule daily 09:00")
    assert schedule.load_all() == [] and "What should it do" in capsys.readouterr().out


def test_schedule_log_and_undo(repo_dir, capsys, monkeypatch):
    import main
    s = schedule.add(make(project=repo_dir))
    before = undo.snapshot(repo_dir)
    (repo_dir / "a.txt").write_text("changed by the run\n")
    monkeypatch.setattr(main.prefs, "signal_done", lambda text: None)
    main.run_scheduled(s.id, run_main=fake_main({"ok": True, "answer": "Changed a.txt.", "before_tree": before,
                                                 "files_changed": [{"path": "a.txt", "change": "modified"}]}))
    app = make_app(repo_dir)
    app.command(f"/schedule log {s.id}")
    out = capsys.readouterr().out
    assert "ok" in out and "a.txt" in out and "Changed a.txt." in out
    app.command(f"/schedule undo {s.id}")
    out = capsys.readouterr().out
    assert "restore: a.txt" in out and (repo_dir / "a.txt").read_text() == "a1\n"


@pytest.fixture
def repo_dir(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (("init", "-q"), ("config", "user.name", "t"), ("config", "user.email", "t@example.com"),
                 ("config", "core.autocrlf", "false")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "a.txt").write_text("a1\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "start"], check=True, capture_output=True)
    return root


# --- the real scheduler (opt-in: it adds and removes one entry on this computer) ----------------------

@pytest.mark.skipif(os.environ.get("CODEX_APP_TEST_REAL_SCHEDULER", "").lower() not in ("1", "yes", "true"),
                    reason="adds a real scheduled task; set CODEX_APP_TEST_REAL_SCHEDULER=yes")
def test_real_scheduler_add_and_remove(tmp_path):
    real = schedule.Scheduler()
    if os.name != "nt" and not __import__("shutil").which("crontab"):
        pytest.skip("no crontab here")
    s = make(project=tmp_path, when=("weekdays", "03:17"))
    s.id = "test" + schedule.new_id()
    try:
        real.add(s)
        assert real.exists(s.id) is True
        if os.name == "nt":
            done = subprocess.run(["schtasks", "/Query", "/TN", f"{schedule.TASK_FOLDER}\\{s.id}", "/XML"],
                                  capture_output=True, text=True)
            assert "--run-schedule" in done.stdout and str(tmp_path) in done.stdout
    finally:
        real.remove(s.id)
    assert real.exists(s.id) is False
