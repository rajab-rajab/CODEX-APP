"""Scheduled tasks (/schedule): Codex App runs a task by itself at set times, like official Codex's
automations, using your computer's own scheduler.

A schedule is saved in ~/.codex-app/schedules.json with the task, the project and the settings it runs
with. The computer's scheduler only starts `main.py --run-schedule <id>`:

- Windows: a Task Scheduler task in the "CodexApp" folder, made from XML, that runs only while you are
  signed in (no password is stored), also on battery, skips a run while the last one is still going,
  catches up a run missed while the computer was off, and is stopped after 4 hours.
- Linux and macOS: a line in your crontab, marked `# codex-app:<id>`.

Each run works like `--json`: its result (answer, files changed, tokens, cost) and its log go to
~/.codex-app/schedules/runs/<id>/, and `/schedule log` shows them. A run never asks anything: there is no
one there to answer. So schedules never use `full` permission (no sandbox) and never use the `ask`
approval mode (deny_all instead), and the browser is off. Before each run a snapshot of the project's
files is taken, so `/schedule undo <id>` can put them back."""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from . import config

TASK_FOLDER = "CodexApp"   # Windows Task Scheduler folder
CRON_TAG = "# codex-app:"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = {d: d for d in DAYS} | {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
                                     "friday": "fri", "saturday": "sat", "sunday": "sun"}
_XML_DAYS = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
             "sat": "Saturday", "sun": "Sunday"}
_CRON_DAYS = {"mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5", "sat": "6", "sun": "0"}
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LOCK_STALE_SECONDS = 6 * 3600  # a lock older than this is from a run that died
RUNS_KEPT = 50                 # per schedule


def home() -> Path:
    return config.APP_HOME


def _file() -> Path:
    return home() / "schedules.json"


def runs_dir(schedule_id: str) -> Path:
    return home() / "schedules" / "runs" / schedule_id


# --- when ---------------------------------------------------------------------------------------------

@dataclass
class When:
    kind: str                    # "daily", "weekly" or "hourly"
    time: str = ""               # "HH:MM" for daily and weekly
    days: tuple = ()             # for weekly: ("mon", "wed")
    every: int = 1               # for hourly: every N hours

    def describe(self) -> str:
        if self.kind == "hourly":
            return "every hour" if self.every == 1 else f"every {self.every} hours"
        if self.kind == "daily":
            return f"every day at {self.time}"
        if self.days == DAYS[:5]:
            return f"weekdays at {self.time}"
        if self.days == DAYS[5:]:
            return f"weekends at {self.time}"
        return f"{', '.join(d.capitalize() for d in self.days)} at {self.time}"


def parse_when(words: list) -> tuple:
    """(When, the words left over: the task) from the start of `/schedule <when> <task>`. Raises
    ValueError with what to write instead.

    daily 09:00 | weekdays 09:00 | weekends 10:30 | mon,wed,fri 18:00 | monday 08:15 | hourly |
    every 3 hours"""
    if not words:
        raise ValueError(USAGE)
    first = words[0].lower()
    if first == "hourly":
        return When("hourly"), words[1:]
    if first == "every":
        if len(words) >= 3 and words[1].isdigit() and words[2].lower() in ("hour", "hours", "h"):
            every = int(words[1])
            if not 1 <= every <= 23:
                raise ValueError("'every N hours' needs N from 1 to 23.")
            return When("hourly", every=every), words[3:]
        raise ValueError("Write 'every 3 hours' (1 to 23).")
    if first == "daily":
        kind, days = "daily", ()
    elif first == "weekdays":
        kind, days = "weekly", DAYS[:5]
    elif first == "weekends":
        kind, days = "weekly", DAYS[5:]
    else:
        names = [d for d in first.split(",") if d]
        if not names or any(d not in _DAY_NAMES for d in names):
            raise ValueError(USAGE)
        kind, days = "weekly", tuple(d for d in DAYS if d in {_DAY_NAMES[n] for n in names})
    if len(words) < 2 or not _TIME.match(words[1]):
        raise ValueError(f"'{first}' needs a time, for example: /schedule {first} 09:00 <task>")
    hour, minute = _TIME.match(words[1]).groups()
    return When(kind, f"{int(hour):02d}:{minute}", days), words[2:]


USAGE = ("Write when, then the task, for example:\n"
         "  /schedule daily 09:00 run the tests and fix what fails\n"
         "  /schedule weekdays 18:00 update the changelog from today's commits\n"
         "  /schedule mon,thu 08:30 check the dependencies for updates\n"
         "  /schedule every 3 hours look for new TODO comments and list them")


# --- saved schedules ----------------------------------------------------------------------------------

@dataclass
class Schedule:
    id: str
    task: str
    project: str
    when: dict                   # When as a dict
    mode: str = "single"
    settings: dict = field(default_factory=dict)  # permission, approval, power, profile, models, team_size
    created: str = ""

    @property
    def timing(self) -> When:
        data = dict(self.when)
        data["days"] = tuple(data.get("days") or ())
        return When(**data)


# The settings a schedule keeps from the session that made it.
KEPT = ("permission", "approval", "power", "profile", "single_model", "single_effort", "planner_model",
        "worker_model", "team_size", "web_search")


def settings_for(settings) -> dict:
    """What a scheduled run uses, from the session's settings: never full permission, never ask."""
    kept = {name: getattr(settings, name) for name in KEPT}
    if kept["permission"] == "full":
        raise ValueError("Schedules can't use 'full' permission (no sandbox): no one is there to watch. "
                         "Switch to /permission workspace first.")
    if kept["approval"] == "ask":
        kept["approval"] = "deny_all"  # no one is there to answer
    return kept


def load_all() -> list:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        try:
            out.append(Schedule(**item))
        except TypeError:
            continue  # from a newer or broken version: leave it out
    return out


def _save_all(items: list) -> None:
    _file().parent.mkdir(parents=True, exist_ok=True)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(s) for s in items], indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _file())


def get(schedule_id: str) -> Optional[Schedule]:
    return next((s for s in load_all() if s.id == schedule_id), None)


def new_id() -> str:
    return secrets.token_hex(3)


# --- the computer's scheduler -------------------------------------------------------------------------

def python_for_runs() -> str:
    """The Python that runs scheduled tasks. On Windows pythonw.exe, so no console window opens."""
    exe = Path(sys.executable)
    if os.name == "nt":
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            return str(quiet)
    return str(exe)


def main_script() -> Path:
    return Path(__file__).resolve().parents[1] / "main.py"


def command(schedule_id: str) -> list:
    return [python_for_runs(), str(main_script()), "--run-schedule", schedule_id]


def windows_xml(s: Schedule, cmd: list, now: Optional[datetime] = None) -> str:
    """A Task Scheduler task: runs while you are signed in, also on battery, one run at a time."""
    when = s.timing
    now = now or datetime.now()
    if when.kind == "hourly":
        start = now.replace(minute=0, second=0, microsecond=0)
        trigger = (f"<TimeTrigger><StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>"
                   f"<Repetition><Interval>PT{when.every}H</Interval></Repetition><Enabled>true</Enabled></TimeTrigger>")
    else:
        hour, minute = when.time.split(":")
        start = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if when.kind == "daily":
            how = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
        else:
            days = "".join(f"<{_XML_DAYS[d]} />" for d in when.days)
            how = f"<ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
        trigger = (f"<CalendarTrigger><StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>"
                   f"<Enabled>true</Enabled>{how}</CalendarTrigger>")
    args = subprocess.list2cmdline(cmd[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>Codex App</Author>
    <Description>{escape(f"Codex App schedule {s.id}: {s.task[:200]}")}</Description>
  </RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(cmd[0])}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(s.project)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def cron_line(s: Schedule, cmd: list) -> str:
    when = s.timing
    if when.kind == "hourly":
        spec = "0 * * * *" if when.every == 1 else f"0 */{when.every} * * *"
    else:
        hour, minute = when.time.split(":")
        days = "*" if when.kind == "daily" else ",".join(_CRON_DAYS[d] for d in when.days)
        spec = f"{int(minute)} {int(hour)} * * {days}"
    import shlex
    return f"{spec} {' '.join(shlex.quote(c) for c in cmd)} >/dev/null 2>&1 {CRON_TAG}{s.id}"


def _run(args: list, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          input=input_text if input_text is not None else "", timeout=60)


class Scheduler:
    """The computer's own scheduler. Replaced in tests."""

    def add(self, s: Schedule) -> None:
        cmd = command(s.id)
        if os.name == "nt":
            path = home() / "schedules" / f"{s.id}.xml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(windows_xml(s, cmd), encoding="utf-16")
            try:
                done = _run(["schtasks", "/Create", "/TN", f"{TASK_FOLDER}\\{s.id}", "/XML", str(path), "/F"])
            finally:
                path.unlink(missing_ok=True)
            if done.returncode:
                raise RuntimeError((done.stderr or done.stdout).strip() or "schtasks failed")
            return
        if shutil.which("crontab") is None:
            raise RuntimeError("crontab isn't installed, so schedules can't be made here")
        lines = [line for line in self._crontab() if not line.endswith(f"{CRON_TAG}{s.id}")]
        self._write_crontab(lines + [cron_line(s, cmd)])

    def remove(self, schedule_id: str) -> None:
        if os.name == "nt":
            done = _run(["schtasks", "/Delete", "/TN", f"{TASK_FOLDER}\\{schedule_id}", "/F"])
            if done.returncode and "cannot find" not in (done.stderr or "").lower():
                raise RuntimeError((done.stderr or done.stdout).strip() or "schtasks failed")
            return
        if shutil.which("crontab") is None:
            return
        self._write_crontab([line for line in self._crontab() if not line.endswith(f"{CRON_TAG}{schedule_id}")])

    def exists(self, schedule_id: str) -> Optional[bool]:
        """True or False, or None if it can't be told."""
        try:
            if os.name == "nt":
                return _run(["schtasks", "/Query", "/TN", f"{TASK_FOLDER}\\{schedule_id}"]).returncode == 0
            if shutil.which("crontab") is None:
                return None
            return any(line.endswith(f"{CRON_TAG}{schedule_id}") for line in self._crontab())
        except (OSError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def _crontab() -> list:
        done = _run(["crontab", "-l"])
        return [line for line in done.stdout.splitlines() if line.strip()] if done.returncode == 0 else []

    @staticmethod
    def _write_crontab(lines: list) -> None:
        done = _run(["crontab", "-"], input_text="\n".join(lines) + "\n")
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or "crontab failed")


SCHEDULER = Scheduler()


def add(s: Schedule) -> Schedule:
    """Save the schedule and register it with the computer's scheduler (all or nothing)."""
    SCHEDULER.add(s)
    items = [x for x in load_all() if x.id != s.id] + [s]
    try:
        _save_all(items)
    except OSError:
        SCHEDULER.remove(s.id)
        raise
    return s


def remove(schedule_id: str) -> bool:
    items = load_all()
    if not any(s.id == schedule_id for s in items):
        return False
    SCHEDULER.remove(schedule_id)
    _save_all([s for s in items if s.id != schedule_id])
    return True


# --- runs ---------------------------------------------------------------------------------------------

class Lock:
    """One run of a schedule at a time (Task Scheduler does this too; cron doesn't)."""

    def __init__(self, schedule_id: str):
        self.path = home() / "schedules" / f"{schedule_id}.lock"
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    old = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if old < LOCK_STALE_SECONDS:
                    return self
                self.path.unlink(missing_ok=True)  # left by a run that died
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.held = True
            return self
        return self

    def __exit__(self, *exc):
        if self.held:
            self.path.unlink(missing_ok=True)


class HiddenWindows:
    """While a scheduled run goes, programs it starts (the Codex engine, git, commands) get a console with
    no window. A scheduled run is pythonw.exe, which has no console, so on Windows each of them would
    otherwise open its own terminal window. Their own children share that hidden console. Elsewhere: no
    change."""

    def __enter__(self):
        self.original = None
        if os.name != "nt":
            return self
        original = self.original = subprocess.Popen.__init__
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        visible = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10) | getattr(subprocess, "DETACHED_PROCESS", 0x8)

        def init(popen, *args, **kwargs):
            flags = kwargs.get("creationflags", 0) or 0
            if not flags & visible:
                kwargs["creationflags"] = flags | no_window
            original(popen, *args, **kwargs)
        subprocess.Popen.__init__ = init
        return self

    def __exit__(self, *exc):
        if self.original is not None:
            subprocess.Popen.__init__ = self.original


def save_run(schedule_id: str, report: dict, log: str) -> Path:
    """Keep one run's result and log; only the newest RUNS_KEPT are kept."""
    folder = runs_dir(schedule_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{stamp}.json"
    path.write_text(json.dumps({**report, "log": log[-20_000:]}, indent=2, ensure_ascii=False), encoding="utf-8")
    for old in sorted(folder.glob("*.json"))[:-RUNS_KEPT]:
        old.unlink(missing_ok=True)
    return path


def runs(schedule_id: str) -> list:
    """[(path, report)] of a schedule's runs, newest first."""
    out = []
    for path in sorted(runs_dir(schedule_id).glob("*.json"), reverse=True):
        try:
            out.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def argv_for(s: Schedule) -> list:
    """The command-line arguments a scheduled run uses (main.py's own --json run)."""
    st = s.settings
    argv = ["--project", s.project, "--mode", s.mode, "--json", "--permission", st.get("permission", "workspace"),
            "--approval", st.get("approval", "deny_all")]
    for flag, key in (("--power", "power"), ("--profile", "profile"), ("--model", "single_model"),
                      ("--effort", "single_effort"), ("--planner-model", "planner_model"),
                      ("--worker-model", "worker_model")):
        if st.get(key):
            argv += [flag, str(st[key])]
    if st.get("team_size"):
        argv += ["--team-size", str(st["team_size"])]
    if st.get("web_search"):
        argv.append("--search")
    return argv + ["--", s.task]


def env_for(s: Schedule) -> dict:
    """Environment for a scheduled run: no browser, no update check, no finish beep (the run sends its
    own notification)."""
    return {"CODEX_APP_UPDATE_CHECK": "no", "CODEX_APP_BROWSER": "no", "CODEX_APP_NOTIFY": "no",
            "CODEX_APP_SCHEDULED": s.id}
