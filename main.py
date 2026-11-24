"""Codex App: OpenAI's Codex (github.com/openai/codex) in your terminal, with two modes.

    py main.py --project "C:\\path\\to\\project"                  A) single model mode (default)
    py main.py --project "C:\\path\\to\\project" --mode dual      B) planner + worker mode
    py main.py --project . --continue                            carry on with the last conversation
    py main.py --project . "add a test for the login page"        one task, then exit

Type /help while it runs for the list of commands.
"""

import argparse
import collections
import copy
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from codex_app import __version__, approvals, engine, gitflow, prefs, profiles, protect, sessions, undo, updater
from codex_app.config import APP_HOME, APPROVALS, EFFORT_LEVELS, PERMISSION_ALIASES, PERMISSIONS, Settings, \
    permission_name
from codex_app.modes import INIT_TASK, MODES, make_reviewer, make_writer, project_instructions, review_task, \
    write_message

GREY, CYAN, YELLOW, RED, GREEN, RESET = "\033[90m", "\033[36m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
DIFF_LINES_SHOWN = 80

HELP = """Commands (most are the same as official Codex's):
  /mode single|dual|team  A) one model does everything, B) planner + worker,
                          C) planner + several workers at the same time
  /goal [--budget N] <goal>  Codex works on its own, over many turns, until the goal is done
                          (optionally stopping after about N tokens, for example --budget 500k)
  /power normal|high|max  stronger models and more reasoning for every role
  /profile auto|coding|chat|maths  how messages are handled (auto: chosen for each message)
  /model [name [effort]]  show or switch the model and reasoning effort
                          (dual/team mode: /model planner|worker <name> [effort])
  /permission <mode>      plan | workspace | full  (official names work too: read-only,
                          workspace-write, danger-full-access)
  /approval <mode>        deny_all | auto_review | ask  (what happens when Codex wants to leave
                          the sandbox; starts a new conversation)
  /status                 models, permission, account, versions, tokens and context left
  /undo                   put back the files and the conversation as they were before your last
                          message (again to go further back)
  /diff                   the project's git changes, including new files
  /history [number]       Codex's conversations for this project, from any Codex client
                          (official Codex CLI too); a number carries on with one
  /commit [message]       commit the changes; Codex writes the message unless you give one
  /pr [title]             push to a branch and open a pull request (needs GitHub's gh)
  /save [show|clear]      remember this project's mode, power, profile, permission and models
  /review [what]          a read-only code review: uncommitted (default), branch <base>,
                          commit <sha>, or your own instructions
  /mention <file>         attach a file to your next message (or write @path in a message)
  /image <file>           attach an image to your next message
  /skills                 list the skills Codex can use; write $name in a message to use one
  /mcp                    list the MCP servers (external tools) Codex is set up with
  /secrets [on|off]       list secret files in the project; turn the secret guard on or off
  /compact                summarise the conversation so far, to free up context
  /init                   write an AGENTS.md that describes this project
  /models                 list the models Codex can use
  /usage                  tokens used (and cost, if prices are set in config.py)
  /new, /clear            start a new conversation
  /fork                   carry on in a copy of this conversation, keeping the original
  /rename <title>         rename this conversation
  /sessions               list saved conversations for this project
  /resume [number]        go back to a saved conversation (default: the most recent)
  /login, /logout         sign in to Codex with your ChatGPT account, or sign out
  /update                 install the newest Codex App from GitHub (and say if the engine has one)
  /version                show the app and Codex engine versions, and check for newer ones
  /exit, /quit            quit
While Codex works, type a message to steer it (added to what it's doing), /queue <task> to line up
the next task, or /stop to stop it.
Ctrl+C stops the current task too."""


def colour_diff(diff: str, limit: int = DIFF_LINES_SHOWN) -> str:
    lines = diff.rstrip("\n").splitlines()
    out = []
    for line in lines[:limit]:
        if line.startswith(("+++", "---", "diff ", "index ")):
            out.append(f"{GREY}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("+"):
            out.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"{RED}{line}{RESET}")
        else:
            out.append(line)
    if len(lines) > limit:
        out.append(f"{GREY}... {len(lines) - limit} more lines (/diff shows them all){RESET}")
    return "\n".join(out)


class Screen:
    """Prints what the agents are doing."""

    def __init__(self):
        self.streaming_from = None
        self.lock = threading.RLock()  # team mode: several agents report at the same time

    def finish(self):
        with self.lock:
            if self.streaming_from:
                print()
                self.streaming_from = None

    def event(self, agent: str, kind: str, text: str) -> None:
        with self.lock:
            self._event(agent, kind, text)

    def _event(self, agent: str, kind: str, text: str) -> None:
        if kind == "text":
            if self.streaming_from != agent:
                self.finish()
                print(f"\n{CYAN}[{agent}]{RESET} ", end="")
                self.streaming_from = agent
            print(text, end="", flush=True)
            return
        self.finish()
        if kind == "thinking":
            print(f"{GREY}  ({agent} thinking: {text.strip()[:160]}){RESET}")
        elif kind == "tool":
            print(f"{YELLOW}  > {agent}: {text}{RESET}")
        elif kind == "tool_result":
            print(f"{GREY}    {text[:160]}{RESET}")
        elif kind == "tool_error":
            print(f"{RED}    {text[:200]}{RESET}")
        elif kind == "plan":
            print(f"{CYAN}  {agent}'s plan:{RESET}")
            for line in text.splitlines():
                print(f"{CYAN}    {line}{RESET}")
        elif kind == "diff":
            print(f"{GREY}  {agent} changed:{RESET}\n{colour_diff(text)}")
        elif kind == "notice":
            print(f"{GREEN}* {text}{RESET}")

    def ask(self, question: str) -> str:
        self.finish()
        return input(f"{YELLOW}{question}{RESET}")


class InputPump:
    """One thread reads every line you type, so you can type while Codex works (official Codex's
    steering). While a task runs, a line goes to Codex as extra instructions, or answers an approval
    question; otherwise it is the next message or command."""

    def __init__(self, app, read=None):
        self.app, self.read = app, read or (lambda: input())
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                line = self.read()
            except (EOFError, KeyboardInterrupt, OSError, ValueError):
                self.lines.put(None)
                return
            if not self.app.take_line(line):
                self.lines.put(line)

    def get(self, prompt: str = ""):
        """The next line, or None when input has ended. Ctrl+C still works while waiting."""
        if prompt:
            print(prompt, end="", flush=True)
        while True:
            try:
                return self.lines.get(timeout=0.2)
            except queue.Empty:
                continue


LIMIT_FAILURES = {"usageLimitExceeded", "rateLimitExceeded", "sessionBudgetExceeded", "apiKeySpendLimitExceeded",
                  "projectSpendLimitExceeded", "organizationSpendLimitExceeded"}


ORIGINATORS = {"codex_python_sdk": "Codex App", "codex_cli_rs": "Codex CLI", "codex_exec": "codex exec",
               "codex_vscode": "VS Code", "codex_desktop": "Codex app"}


def made_by(thread) -> str:
    """Which Codex client made a conversation, for /history."""
    originator = getattr(thread, "originator", None)
    if originator:
        return ORIGINATORS.get(originator, originator)[:10]
    source = _value_root(getattr(thread, "source", ""))
    return str(getattr(source, "value", source) or "?")[:10]


def conversation_title(thread) -> str:
    """A conversation's name, or its first message without the app's own wrapping ("TASK:", profile notes)."""
    text = str(getattr(thread, "name", None) or getattr(thread, "preview", "") or "")
    text = re.sub(r"^\s*\[[^\]]{0,60}\][^\n]*\n+", "", text)  # [Conversation ...] / [Maths] guidance
    text = re.sub(r"^\s*(TASK|QUESTION[^:]*|GOAL):\s*", "", text)
    return " ".join(text.split())


def review_target(words: list, arg: str) -> dict:
    """The target for Codex's own review, from /review's words (the same four presets as official Codex)."""
    if not words or words[0] == "uncommitted":
        return {"type": "uncommittedChanges"}
    if words[0] == "branch":
        return {"type": "baseBranch", "branch": words[1].strip()}
    if words[0] == "commit":
        return {"type": "commit", "sha": words[1].strip()}
    return {"type": "custom", "instructions": arg}


def parse_budget(text: str):
    """'500k' -> 500000, '1.5m' -> 1500000, '20000' -> 20000; None if it isn't a number."""
    text = text.strip().lower().replace(",", "").replace("_", "")
    scale = {"k": 1_000, "m": 1_000_000}.get(text[-1:], 1)
    try:
        value = float(text[:-1] if scale > 1 else text)
    except ValueError:
        return None
    return int(value * scale) if value > 0 else None


def print_usage(mode, extra=()) -> None:
    for label, u in list(mode.usage()) + list(extra):
        cost = u.cost()
        cost_text = f", about ${cost:.4f}" if cost is not None else ""
        left = u.context_left()
        left_text = f", {left}% context left" if left is not None else ""
        print(f"  {label}: {u.input:,} in ({u.cached:,} cached), {u.output:,} out "
              f"({u.reasoning:,} reasoning){cost_text}{left_text}")


def print_versions() -> None:
    print(f"Codex App {__version__}, Codex engine {updater.engine_version() or 'not installed'}")
    app, engine = updater.check_both()  # GitHub and PyPI at the same time
    print(app.message if app else "Could not check GitHub for a new app version.")
    print(engine.message if engine else "Could not check PyPI for a new Codex engine.")


def git_changes(root: Path) -> str:
    """What official Codex's /diff shows: the git diff of the project, including untracked files."""
    def git(*args):
        # stdin=DEVNULL: on Windows git can hang at start if it inherits the input the steering thread reads.
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=60)
    try:
        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return "This project is not a git repository, so there is no diff to show."
        has_head = git("rev-parse", "--verify", "HEAD").returncode == 0
        diff = git("diff", "HEAD").stdout if has_head else git("diff", "--cached").stdout + git("diff").stdout
        untracked = git("ls-files", "--others", "--exclude-standard").stdout.split("\n")
    except FileNotFoundError:
        return "Git is not installed, so there is no diff to show."
    untracked = [f for f in untracked if f.strip()]
    if not diff.strip() and not untracked:
        return "No changes."
    parts = [colour_diff(diff, limit=100_000)] if diff.strip() else []
    if untracked:
        parts.append("New files (not in git yet):\n" + "\n".join(f"{GREEN}  {f}{RESET}" for f in untracked))
    return "\n".join(parts)


def attachment_for(path_text: str, root: Path, sdk, image: bool = False):
    """An SDK input item for a file: an image, or a file mention. None if there is no such file."""
    path = Path(path_text.strip().strip('"'))
    path = path if path.is_absolute() else root / path
    if not path.is_file():
        return None
    if image or path.suffix.lower() in IMAGE_TYPES:
        return sdk.LocalImageInput(str(path))
    return sdk.MentionInput(path.name, str(path))


def mentions_in(text: str, root: Path, sdk) -> list:
    """@path mentions in a message, like official Codex's @ file mentions. Unknown paths are left alone."""
    found = []
    for match in re.finditer(r'(?<!\S)@("[^"]+"|\S+)', text):
        item = attachment_for(match.group(1).rstrip(".,;:!?)"), root, sdk)
        if item is not None:
            found.append(item)
    return found


def confirm_full(ask) -> bool:
    """'full' turns Codex's sandbox off. Only allow it after the user types yes."""
    print(f"{RED}'full' turns Codex's sandbox off: Codex could change anything your account can, including "
          f"system files and other folders. Use it only if you must.{RESET}")
    if not protect.running_interactively():
        print(f"{RED}Refused: 'full' needs you to confirm it in an interactive terminal.{RESET}")
        return False
    try:
        return ask("Type yes to turn the sandbox off: ").strip().lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        return False


def sign_in(codex) -> bool:
    """Make sure Codex is signed in. Uses an existing Codex sign-in, else OPENAI_API_KEY."""
    try:
        if getattr(codex.account(), "account", None):
            return True
    except Exception:
        pass
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        codex.login_api_key(key)  # the key goes to Codex's own sign-in store, never into a prompt
        print(f"{GREY}Signed in to Codex with your OPENAI_API_KEY.{RESET}")
        return True
    print(f"{YELLOW}Codex is not signed in. Set OPENAI_API_KEY, or type /login to use your ChatGPT account.{RESET}")
    return False


class App:
    def __init__(self, codex, sdk, root, settings: Settings, mode_name: str, approver=None, screen=None,
                 update_notice=None, start_settings=None):
        self.update_notice = update_notice  # an updater.BackgroundNotice started before Codex, or None
        self.start_settings = start_settings or copy.copy(settings)  # what /power normal goes back to
        self.pump = None        # an InputPump once the interactive loop runs
        self._busy = False      # a task or goal is running
        self._answers = None    # a queue while an approval question waits for its answer
        self._steer_hint = True
        self.undo_points = []   # one per message: what /undo goes back to (newest last)
        self._skills = None     # name -> path, once listed
        self.writer = None      # writes commit messages and pull request descriptions
        self._limits_unavailable = ""
        self.queued = collections.deque()  # /queue: tasks waiting for the current one to finish  # why plan limits can't be shown (API-key sign-in), once known
        self._last_message = ""
        self.codex = codex
        self.sdk = sdk
        self.root = Path(root)
        self.settings = settings
        self.screen = screen or Screen()
        self.approver = approver
        self.mode = MODES[mode_name](codex, sdk, self.root, settings, on_event=self.screen.event)
        self.session_path = sessions.new_session_path(mode_name)
        self.title = ""
        self.pending = []    # files and images attached with /mention and /image, sent with the next message
        self.reviewer = None

    def describe(self) -> str:
        s = self.settings
        if self.mode.name == "single":
            mode = f"single model mode ({s.single_model}, {s.single_effort})"
        elif self.mode.name == "dual":
            mode = f"dual mode: planner {s.planner_model} ({s.planner_effort}), worker {s.worker_model} ({s.worker_effort})"
        else:
            mode = (f"team mode: planner {s.planner_model} ({s.planner_effort}), up to {s.team_size} workers "
                    f"{s.worker_model} ({s.worker_effort})")
        return (f"{mode}, power '{s.power}', profile '{s.profile}', permission '{s.permission}', "
                f"approval '{s.approval}'")

    # --- typing while Codex works -------------------------------------------------------------

    def ask(self, question: str) -> str:
        """Ask the user something (approvals, confirmations), through the input pump when it runs."""
        self.screen.finish()
        if self.pump is None:
            return input(f"{YELLOW}{question}{RESET}")
        print(f"{YELLOW}{question}{RESET}", end="", flush=True)
        if threading.current_thread() is threading.main_thread() and not self._busy:
            line = self.pump.get()
            return line or ""
        self._answers = answers = queue.Queue()  # asked from Codex's side while a task runs
        try:
            return answers.get(timeout=900)
        except queue.Empty:
            return ""
        finally:
            self._answers = None

    def take_line(self, line: str) -> bool:
        """Called by the input pump for every line. True if the line was used here (an answer, or
        steering while a task runs); False to make it the next message."""
        if self._answers is not None:
            self._answers.put(line)
            return True
        if not self._busy:
            return False
        text = line.strip()
        if not text:
            return True
        if text in ("/stop", "/interrupt"):
            print(f"{YELLOW}Stopping...{RESET}")
            self.mode.repair()
        elif text == "/queue" or text.startswith("/queue "):
            self.queue(text[len("/queue"):].strip())
        elif text.startswith("/"):
            print(f"{YELLOW}Codex is working. Type a message to steer it, /queue <task> to do it next, or /stop "
                  f"to stop it; other commands wait until it's done.{RESET}")
        else:
            names = self.mode.steer(text)
            print(f"{GREY}  (sent to {', '.join(names)} while it works){RESET}" if names
                  else f"{YELLOW}Nothing to steer right now; send it again when Codex has finished.{RESET}")
        return True

    def new_conversation(self, mode_name: str) -> None:
        self.undo_points = []  # /undo works within one conversation
        self.mode = MODES[mode_name](self.codex, self.sdk, self.root, self.settings, on_event=self.screen.event)
        self.session_path = sessions.new_session_path(mode_name)
        self.title = ""

    def save(self) -> None:
        if not self.mode.is_empty():
            try:
                sessions.save(self.session_path, self.root, self.mode.state(), self.title)
            except OSError as e:
                print(f"{RED}Could not save the conversation: {e}{RESET}")

    def resume(self, path, data) -> None:
        self.new_conversation(data["mode"])
        self.mode.load_state(data)
        self.session_path = path
        self.title = data.get("title", "")
        print(f"Resumed '{self.title or path.name}' ({data['mode']} mode, saved {data.get('saved', '?')}).")

    def check_limits(self, after_task: bool = True) -> list[str]:
        """Warn when a plan or rate limit is nearly used up, or was hit. Returns the lines for /status."""
        if self._limits_unavailable:
            return [] if after_task else [self._limits_unavailable]
        try:
            snapshot = engine.rate_limits(self.codex)
        except Exception as e:
            if "chatgpt authentication required" in str(e).lower():
                # Signed in with an API key: Codex has no plan limits to report (spend limits are set at OpenAI).
                self._limits_unavailable = ("shown only when signed in with a ChatGPT account; with an API key, "
                                            "set a monthly spend limit in your OpenAI account")
                return [] if after_task else [self._limits_unavailable]
            return []
        lines, warnings = engine.describe_limits(snapshot)
        hit = {a.last_failure for a in self.mode.agents()} & LIMIT_FAILURES
        if after_task and hit:
            print(f"{YELLOW}  You've hit a usage limit. " + ("; ".join(lines) or "Try again later.") + RESET)
        elif after_task and warnings:
            print(f"{YELLOW}  Careful: {'; '.join(warnings)}.{RESET}")
        return lines

    def status_line(self) -> None:
        left = self.mode.context_left()
        used = sum(u.input + u.output for _, u in self.mode.usage())
        cost = self.mode.total_cost()
        parts = [f"{used:,} tokens"]
        if left is not None:
            parts.append(f"{left}% context left")
        if cost is not None:
            parts.append(f"about ${cost:.4f}")
        print(f"{GREY}  ({', '.join(parts)}){RESET}")
        if left is not None and left <= self.settings.auto_compact_at and self.settings.auto_compact:
            self.auto_compact(left)
        elif left is not None and left <= 15:
            print(f"{YELLOW}  Context is nearly full: /compact frees it up, /new starts fresh.{RESET}")

    def auto_compact(self, left: int) -> None:
        """Little context left: compact by itself, so long work doesn't stall."""
        try:
            if self.mode.compact():
                print(f"{GREEN}* Only {left}% of the context was left, so Codex summarised the conversation to "
                      f"free it up (CODEX_APP_AUTO_COMPACT=no turns this off).{RESET}")
        except self.sdk.CodexError as e:
            print(f"{YELLOW}  Context is nearly full and compacting failed ({e}): /new starts fresh.{RESET}")

    def _run(self, work) -> None:
        if self.pump is not None and self._steer_hint:
            print(f"{GREY}  (type to steer Codex while it works, /stop to stop it){RESET}")
            self._steer_hint = False
        point = {"mode": self.mode, "title": self._last_message, "tree": undo.snapshot(self.root),
                 "marks": {a.name: len(a.turns_log) for a in self.mode.agents()}}
        started = time.monotonic()
        self._busy = True
        try:
            work()
        except KeyboardInterrupt:
            self.mode.repair()  # tells Codex to stop the running turn
            print(f"\n{YELLOW}Stopped. You can carry on with a new message.{RESET}")
        except self.sdk.CodexError as e:
            print(f"{RED}Codex error: {e}{RESET}")
        finally:
            self._busy = False
            self.screen.finish()
            if any(len(a.turns_log) > point["marks"].get(a.name, 0) for a in self.mode.agents()):
                self.undo_points.append(point)
            self.save()
        self.status_line()
        self.check_limits()
        took = time.monotonic() - started
        if self.settings.notify and took >= self.settings.notify_after:
            minutes, seconds = divmod(int(took), 60)
            prefs.signal_done(f"Finished after {minutes} min {seconds} s: {self._last_message[:80]}")

    def run_task(self, task: str, attachments=()) -> None:
        self.title = self.title or task[:80]
        self._last_message = task
        attachments = [*self.pending, *attachments, *mentions_in(task, self.root, self.sdk), *self.skills_in(task)]
        self.pending = []
        profile = profiles.resolve(self.settings.profile, task)
        if profile != "coding":
            print(f"{GREY}  ({profiles.describe(profile)}){RESET}")
        self._run(lambda: self.mode.run(task, attachments, profile))

    def goal(self, arg: str) -> None:
        budget = None
        words = arg.split()
        if len(words) >= 2 and words[0] == "--budget":
            budget = parse_budget(words[1])
            if budget is None:
                print("Use /goal --budget <tokens> <goal>, for example /goal --budget 500k make all tests pass")
                return
            arg = " ".join(words[2:])
        if not arg.strip():
            print("Use /goal <what Codex should achieve>, for example /goal make all the tests pass")
            return
        self.title = self.title or f"goal: {arg[:74]}"
        self._last_message = f"/goal {arg}"
        print(f"{GREEN}* Goal set{f' (budget about {budget:,} tokens)' if budget else ''}. Codex works on its own "
              f"until it's done; type to steer it, /stop to pause it.{RESET}")
        self._run(lambda: self.mode.run_goal(arg.strip(), budget))

    def login(self) -> None:
        handle = self.codex.login_chatgpt_device_code()
        print(f"Open {handle.verification_url} and enter the code {handle.user_code}")
        print("Waiting for you to finish signing in...")
        print("Signed in." if getattr(handle.wait(), "success", False) else "Sign-in did not complete.")

    # --- commands ------------------------------------------------------------------------------

    def set_model(self, arg: str) -> None:
        words = arg.split()
        role = None
        if self.mode.name in ("dual", "team"):
            if not words or words[0] not in ("planner", "worker"):
                print(f"In {self.mode.name} mode: /model planner <name> [effort]  or  /model worker <name> [effort]")
                return
            role, words = words[0], words[1:]
        if not words or len(words) > 2:
            print("Use /model <name> [effort]. /models lists the models.")
            return
        model, effort = words[0], (words[1] if len(words) == 2 else None)
        if effort and effort not in EFFORT_LEVELS:
            print(f"Effort must be one of: {', '.join(EFFORT_LEVELS)}")
            return
        s = self.settings
        if role is None:
            s.single_model, s.single_effort = model, effort or s.single_effort
            agent = self.mode.agent
        elif role == "planner":
            s.planner_model, s.planner_effort = model, effort or s.planner_effort
            agent = self.mode.planner
        else:
            s.worker_model, s.worker_effort = model, effort or s.worker_effort
            for worker in self.mode.workers()[1:]:
                worker.set_model(model, effort)
            agent = self.mode.worker
        agent.set_model(model, effort)
        print(f"{agent.name if role != 'worker' or self.mode.name != 'team' else 'the workers'} now use"
              f"{'s' if role != 'worker' or self.mode.name != 'team' else ''} {agent.model} ({agent.effort}), "
              "from the next message on.")

    def set_power(self, level: str) -> None:
        if level not in profiles.POWER:
            print("Use /power normal, /power high or /power max")
            return
        s = self.settings
        profiles.apply_power(s, level, self.start_settings)
        mode = self.mode
        if mode.name == "single":
            mode.agent.set_model(s.single_model, s.single_effort)
        else:
            mode.planner.set_model(s.planner_model, s.planner_effort)
            for worker in mode.workers():
                worker.set_model(s.worker_model, s.worker_effort)
        self.reviewer = None
        print(f"Power is now '{level}': {self.describe()}. It costs more tokens the higher it goes."
              if level != "normal" else f"Power is back to 'normal': {self.describe()}.")

    def undo(self) -> None:
        """Official Codex's undo: the files and the conversation go back to before the last message."""
        if not self.undo_points:
            print("Nothing to undo in this conversation.")
            return
        point = self.undo_points[-1]
        if point["mode"] is not self.mode:
            self.undo_points.clear()
            print("Nothing to undo in this conversation.")
            return
        later = {a.name: a.turns_log[point["marks"].get(a.name, 0):] for a in self.mode.agents()}
        if point["tree"]:
            files = undo.changes(self.root, point["tree"])
            plan = [f"  {what:10} {path}" for what, path in files]
        else:
            diffs = [d for turns in later.values() for _, d in turns if d]
            files = diffs
            plan = [f"  reverse the changes Codex reported ({len(diffs)} diff{'s' if len(diffs) != 1 else ''}; "
                    "no git, so files changed in other ways stay as they are)"] if diffs else []
        print(f"Undo your last message: {point['title'][:100]!r}?")
        if plan:
            print("Files:\n" + "\n".join(plan[:20]) + (f"\n  ... and {len(plan) - 20} more" if len(plan) > 20 else ""))
        else:
            print("Files: nothing to change.")
        print("Codex's conversation goes back to before that message too.")
        if self.ask("Undo it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing was undone.")
            return
        try:
            if point["tree"]:
                undo.restore(self.root, point["tree"])
            elif files:
                undo.reverse_diffs(self.root, files)
        except Exception as e:
            print(f"{RED}The files couldn't be put back: {e}. Nothing was undone.{RESET}")
            return
        problems = []
        for agent in self.mode.agents():
            try:
                agent.undo_turns(point["marks"].get(agent.name, 0))
            except Exception as e:
                problems.append(f"{agent.name}: {e}")
        self.undo_points.pop()
        self.save()
        print(f"{GREEN}* Undone: the files and the conversation are back to before that message.{RESET}"
              if not problems else
              f"{YELLOW}The files are back, but the conversation couldn't be wound back ({'; '.join(problems)}). "
              f"/new starts a fresh one.{RESET}")

    def skills_in(self, text: str) -> list:
        """$name in a message attaches that skill, as in official Codex."""
        names = re.findall(r"(?<![\w$])\$([A-Za-z][\w.-]*)", text or "")
        if not names:
            return []
        if self._skills is None:
            try:
                found, _ = engine.skills(self.codex, str(self.root))
                self._skills = {str(k.name): str(k.path) for k in found if getattr(k, "enabled", True)}
            except Exception:
                self._skills = {}
        return [self.sdk.SkillInput(n, self._skills[n]) for n in dict.fromkeys(names) if n in self._skills]

    def show_skills(self) -> None:
        try:
            found, errors = engine.skills(self.codex, str(self.root), reload=True)
        except Exception as e:
            print(f"{RED}Could not list skills: {e}{RESET}")
            return
        self._skills = {str(k.name): str(k.path) for k in found if getattr(k, "enabled", True)}
        if not found:
            print("Codex has no skills for this project yet. A skill is a folder with a SKILL.md that tells "
                  "Codex how to do a kind of task; see the official Codex docs on skills.")
        for k in sorted(found, key=lambda k: str(k.name)):
            scope = getattr(getattr(k, "scope", None), "value", getattr(k, "scope", ""))
            off = "" if getattr(k, "enabled", True) else "  (turned off)"
            about = " ".join(str(getattr(k, "short_description", None) or k.description or "").split())
            about = about[:90] + ("..." if len(about) > 90 else "")
            print(f"  ${k.name:24} {about}  [{scope}]{off}")
        for e in errors:
            print(f"{YELLOW}  could not load {e.path}: {e.message}{RESET}")
        if found:
            print("Write $name in a message to have Codex use that skill.")

    def show_mcp(self) -> None:
        thread_id = next((a.thread_id for a in self.mode.agents() if a.thread_id), None)
        try:
            servers = engine.mcp_servers(self.codex, thread_id)
        except Exception as e:
            print(f"{RED}Could not list MCP servers: {e}{RESET}")
            return
        if not servers:
            print("No MCP servers are set up. MCP servers give Codex extra tools (GitHub, databases, browsers ...).\n"
                  "Add one to Codex's config file, ~/.codex/config.toml, for example:\n"
                  "  [mcp_servers.NAME]\n  command = \"npx\"\n  args = [\"-y\", \"package-name\"]\n"
                  "or for one run: -c mcp_servers.NAME.command=... Then restart the app.")
            return
        for server in servers:
            status = getattr(getattr(server, "runtime_status", None), "value", None) or "unknown"
            auth = getattr(getattr(server, "auth_status", None), "value", "")
            tools = sorted((server.tools or {}).keys())
            print(f"  {server.name}: {status}" + (f", sign-in: {auth}" if auth not in ("", "unknown", "unsupported") else "")
                  + f", {len(tools)} tool{'s' if len(tools) != 1 else ''}")
            if tools:
                print("    " + ", ".join(tools[:30]) + (" ..." if len(tools) > 30 else ""))
            if getattr(server, "tools_error", None):
                print(f"{YELLOW}    {server.tools_error}{RESET}")

    def warn_secrets(self) -> None:
        found = protect.find_secret_files(self.root)
        if found:
            print(f"{YELLOW}Secret files in this project: {', '.join(found[:6])}{' ...' if len(found) > 6 else ''}\n"
                  f"Codex is told never to touch them"
                  f"{', and the secret guard stops it if it starts to' if self.settings.secret_guard else ''}. "
                  f"For full safety keep them outside the project folder. (/secrets lists them){RESET}")

    def secrets(self, arg: str) -> None:
        if arg in ("on", "off"):
            self.settings.secret_guard = arg == "on"
            print("The secret guard is on: a turn stops the moment Codex starts to read or change a secret file."
                  if arg == "on" else f"{YELLOW}The secret guard is off. Codex is still told not to touch secret "
                                      f"files, but nothing stops it.{RESET}")
            return
        found = protect.find_secret_files(self.root, limit=200)
        print("Secret files in this project:" if found else "No secret files found in this project.")
        for path in found:
            print(f"  {path}")
        print(f"Secret guard: {'on' if self.settings.secret_guard else 'off'} (/secrets on|off)")

    def _write(self, what: str, text: str) -> tuple[str, str]:
        model = self.settings.worker_model if self.mode.name != "single" else self.settings.single_model
        if self.writer is None or self.writer.model != model:
            self.writer = make_writer(self.codex, self.sdk, self.root, self.settings, model, on_event=self.screen.event)
        self.writer.reset()  # each message is written in a fresh, read-only conversation
        try:
            return write_message(self.writer, what, text)
        finally:
            self.writer.discard()  # kept out of Codex's history

    def commit(self, arg: str = "") -> bool:
        """/commit: commit the changes, never secret files. Codex writes the message unless one is given.
        True if a commit was made."""
        if not gitflow.is_repo(self.root):
            print("This project is not a git repository. Run 'git init' first to use /commit.")
            return False
        try:
            files = gitflow.changed_files(self.root)
        except gitflow.GitError as e:
            print(f"{RED}git: {e}{RESET}")
            return False
        paths, secrets = gitflow.split_secrets(files)
        if secrets:
            print(f"{YELLOW}Not committed, because they may hold secrets: {', '.join(secrets)}. "
                  f"Add them to .gitignore.{RESET}")
        if not paths:
            print("Nothing to commit.")
            return False
        status = {path: st for st, path in files}
        print("Changes to commit:\n" + "\n".join(f"  {status[p]:2} {p}" for p in paths[:25])
              + (f"\n  ... and {len(paths) - 25} more" if len(paths) > 25 else ""))
        if arg.strip():
            subject, body = arg.strip(), ""
        else:
            print(f"{GREEN}* Codex is writing the commit message.{RESET}")
            try:
                subject, body = self._write("git commit message", gitflow.diff_text(self.root, paths))
            except (self.sdk.CodexError, gitflow.GitError) as e:
                print(f"{RED}Could not write a message ({e}). Use /commit <message>.{RESET}")
                return False
            finally:
                self.screen.finish()
        print(f"Message:\n  {subject}" + ("".join(f"\n  {line}" for line in body.splitlines()) if body else ""))
        answer = self.ask("Commit with this message? [y/e(dit)/N] ").strip().lower()
        if answer in ("e", "edit"):
            typed = self.ask("Your commit message (one line): ").strip()
            if not typed:
                print("Nothing was committed.")
                return False
            subject, body = typed, ""
        elif answer not in ("y", "yes"):
            print("Nothing was committed.")
            return False
        try:
            short = gitflow.commit(self.root, f"{subject}\n\n{body}" if body else subject, paths)
        except gitflow.GitError as e:
            print(f"{RED}The commit failed: {e}{RESET}")
            return False
        print(f"{GREEN}* Committed {short}: {subject}{RESET}")
        return True

    def pull_request(self, arg: str = "") -> None:
        """/pr: push the work to a branch and open a pull request, after asking."""
        if not gitflow.is_repo(self.root):
            print("This project is not a git repository.")
            return
        if not gitflow.has_remote(self.root):
            print("This repository has no 'origin' remote on GitHub to open a pull request on.")
            return
        if not gitflow.gh_available():
            print("Opening a pull request needs GitHub's gh tool: install it from https://cli.github.com "
                  "and run 'gh auth login'.")
            return
        try:
            paths, _ = gitflow.split_secrets(gitflow.changed_files(self.root))
            if paths:
                print("There are uncommitted changes; committing them first.")
                if not self.commit():
                    print("No pull request was opened.")
                    return
            base, branch = gitflow.default_branch(self.root), gitflow.current_branch(self.root)
            ahead = gitflow.commits_ahead(self.root, base)
        except gitflow.GitError as e:
            print(f"{RED}git: {e}{RESET}")
            return
        if not ahead:
            print(f"There are no commits to put in a pull request: this branch matches {base} on GitHub.")
            return
        if arg.strip():
            title, body = arg.strip(), ""
        elif len(ahead) == 1:
            title, body = gitflow.last_commit_message(self.root)
        else:
            print(f"{GREEN}* Codex is writing the pull request description.{RESET}")
            try:
                log = "\n".join(f"- {subject}" for _, subject in ahead)
                title, body = self._write("pull request title (as subject) and description (as body)",
                                          f"Commits:\n{log}\n\n{gitflow.diff_text(self.root, ['.'])}")
            except (self.sdk.CodexError, gitflow.GitError) as e:
                print(f"{RED}Could not write a description ({e}). Use /pr <title>.{RESET}")
                return
            finally:
                self.screen.finish()
        new_branch = branch == base or branch == "HEAD"
        target = gitflow.branch_name(title, self.root) if new_branch else branch
        print(f"Pull request into {base}: {title}")
        print(f"  {len(ahead)} commit{'s' if len(ahead) != 1 else ''}: " + "; ".join(s for _, s in ahead[:5])
              + (" ..." if len(ahead) > 5 else ""))
        if new_branch:
            print(f"  Your commits move to a new branch, {target}, and {base} goes back to match GitHub "
                  "(nothing is lost: the commits are on the new branch).")
        if self.ask(f"Push {target} to GitHub and open the pull request? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing was pushed.")
            return
        try:
            if new_branch:
                gitflow.move_to_branch(self.root, target, base)
            gitflow.push(self.root, target)
            url = gitflow.create_pr(self.root, base, target, title, body)
        except gitflow.GitError as e:
            print(f"{RED}Could not open the pull request: {e}{RESET}")
            return
        print(f"{GREEN}* Pull request opened: {url}{RESET}")

    def save_prefs(self, arg: str = "") -> None:
        """/save: remember this project's settings; /save show; /save clear."""
        if arg == "clear":
            print("Forgot this project's saved preferences." if prefs.clear(self.root)
                  else "This project has no saved preferences.")
            return
        if arg == "show":
            saved = prefs.load(self.root)
            print("Saved for this project: " + (", ".join(f"{k}={v}" for k, v in saved.items()) if saved else "nothing"))
            return
        s, start = self.settings, self.start_settings
        values = {"mode": self.mode.name, "power": s.power, "profile": s.profile, "permission": s.permission,
                  "approval": s.approval, "team_size": s.team_size, "web_search": s.web_search, "notify": s.notify,
                  # the models without the power preset on top, so loading applies the power level again
                  **{k: getattr(start if s.power != "normal" else s, k) for k in
                     ("single_model", "single_effort", "planner_model", "planner_effort", "worker_model",
                      "worker_effort")}}
        saved = prefs.save(self.root, values)
        print("Saved for this project, used from the next start: " + ", ".join(f"{k}={v}" for k, v in saved.items()))
        if s.permission == "full":
            print(f"{YELLOW}The 'full' permission still needs you to type yes at each start.{RESET}")

    def set_profile(self, arg: str) -> None:
        if arg not in profiles.PROFILES:
            print("Use /profile auto, coding, chat or maths. auto picks one for each message.")
            return
        self.settings.profile = arg
        print(f"Profile is now '{arg}'" + (": chosen for each message." if arg == "auto" else "."))

    def list_models(self) -> None:
        try:
            listing = self.codex.models()
            for m in getattr(listing, "data", None) or []:
                print(f"  {getattr(m, 'id', None) or getattr(m, 'model', m)}")
        except self.sdk.CodexError as e:
            print(f"{RED}Could not list models: {e}{RESET}")

    def show_status(self) -> None:
        s = self.settings
        name, _ = project_instructions(self.root)
        try:
            account = _value_root(getattr(self.codex.account(), "account", None))
            account_text = (getattr(account, "email", None) or _value_root(getattr(account, "type", None))
                            or "signed in") if account else "not signed in"
        except Exception:
            account_text = "unknown"
        print(f"Codex App {__version__}, Codex engine {updater.engine_version() or 'not installed'}")
        print(f"  Project:     {self.root}{f' (Codex reads {name})' if name else ' (no AGENTS.md)'}")
        print(f"  Mode:        {self.describe()}")
        print(f"  Sandbox:     {PERMISSIONS[s.permission]}")
        print(f"  Approval:    {APPROVALS[s.approval]}")
        print(f"  Web search:  {'live' if s.web_search else 'off'}")
        print(f"  Account:     {account_text}")
        limits = self.check_limits(after_task=False)
        if limits:
            print(f"  Limits:      {'; '.join(limits)}")
        saved = prefs.load(self.root)
        print(f"  Saved prefs: {', '.join(f'{k}={v}' for k, v in saved.items()) if saved else 'none (/save remembers them)'}")
        print(f"  Saved as:    {self.session_path.name}{f'  ({self.title})' if self.title else ''}")
        for agent in self.mode.agents():
            print(f"  {agent.name:11}  thread {agent.thread_id or '(not started)'}")
        print_usage(self.mode, self.extra_usage())

    def extra_usage(self):
        extra = [(f"reviewer ({self.reviewer.model})", self.reviewer.usage)] if self.reviewer else []
        if self.writer:
            extra.append((f"writer ({self.writer.model})", self.writer.usage))
        return extra

    def review(self, arg: str) -> None:
        words = arg.split(maxsplit=1)
        if not words or words[0] == "uncommitted":
            task = review_task("uncommitted")
        elif words[0] in ("branch", "commit") and len(words) == 2:
            task = review_task(words[0], words[1].strip())
        elif words[0] in ("branch", "commit"):
            print(f"Use /review {words[0]} <{'base branch' if words[0] == 'branch' else 'commit'}>")
            return
        else:
            task = review_task("custom", arg)
        model = self.settings.planner_model if self.mode.name != "single" else self.settings.single_model
        if self.reviewer is None or self.reviewer.model != model:
            self.reviewer = make_reviewer(self.codex, self.sdk, self.root, self.settings, model,
                                          on_event=self.screen.event)
        self.reviewer.reset()  # each review is a fresh, read-only conversation
        print(f"{GREEN}* Reviewing (read-only, {model}).{RESET}")
        try:
            try:
                # Codex's own review, as in official Codex; the app's review instructions if it can't.
                self.reviewer.run_review(review_target(words, arg))
            except (self.sdk.CodexError, AttributeError, TypeError) as e:
                print(f"{GREY}  (Codex's own review isn't available here ({e}); using the app's.){RESET}")
                self.reviewer.send(task)  # same fresh, read-only conversation
            finally:
                self.reviewer.discard()  # kept out of Codex's history
        except KeyboardInterrupt:
            self.reviewer.interrupt()
            print(f"\n{YELLOW}Review stopped.{RESET}")
        except self.sdk.CodexError as e:
            print(f"{RED}Codex error: {e}{RESET}")
        finally:
            self.screen.finish()

    def queue(self, arg: str) -> None:
        """/queue <task>: do it after the current task (or now, if nothing runs). /queue lists, /queue clear empties."""
        if arg == "clear":
            self.queued.clear()
            print("The queue is empty.")
        elif arg:
            self.queued.append(arg)
            print(f"{GREY}  (queued; {len(self.queued)} waiting){RESET}" if self._busy else
                  f"{GREY}  (queued){RESET}")
        elif self.queued:
            print("Waiting:\n" + "\n".join(f"  {n}. {task}" for n, task in enumerate(self.queued, 1)))
        else:
            print("Nothing is queued. While Codex works, /queue <task> lines up the next one.")

    def history(self, arg: str) -> None:
        """/history: Codex's own conversations for this project, from every Codex client (this app, the
        official Codex CLI, VS Code ...). /history <number> carries on with one, in single mode."""
        try:
            found = engine.threads(self.codex, str(self.root), limit=20)
        except Exception as e:
            print(f"{RED}Could not list Codex's conversations: {e}{RESET}")
            return
        found = [t for t in found if not getattr(t, "ephemeral", False)]
        if not arg:
            if not found:
                print("Codex has no saved conversations for this project folder yet.")
            for n, t in enumerate(found, 1):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime((getattr(t, "updated_at", 0) or 0)))
                about = conversation_title(t)[:70]
                print(f"  {n:2}. {when}  {made_by(t):10}  {about or t.id}")
            if found:
                print("/history <number> carries on with one (in single mode).")
            return
        if not arg.isdigit() or not 1 <= int(arg) <= len(found):
            print("Use /history to list them, then /history <number>.")
            return
        chosen = found[int(arg) - 1]
        self.save()
        self.new_conversation("single")
        self.mode.agent.thread_id = chosen.id  # resumed (with this app's settings) on the next message
        self.title = conversation_title(chosen)[:80]
        print(f"Carrying on with '{self.title or chosen.id}' in single mode. Your next message continues it.")

    def attach(self, arg: str, image: bool) -> None:
        if not arg:
            print(f"Use /{'image' if image else 'mention'} <file>")
            return
        item = attachment_for(arg, self.root, self.sdk, image=image)
        if item is None:
            print(f"{RED}No such file: {arg}{RESET}")
            return
        self.pending.append(item)
        print(f"Attached {arg}; it goes with your next message.")

    def set_permission(self, arg: str) -> None:
        arg = permission_name(arg)
        if arg not in PERMISSIONS:
            names = "\n".join(f"  {k:9} {v}" for k, v in PERMISSIONS.items())
            aliases = ", ".join(f"{k} = {v}" for k, v in PERMISSION_ALIASES.items())
            print(f"Use /permission with one of:\n{names}\nOfficial Codex's names work too: {aliases}")
            return
        if arg == "full" and self.settings.permission != "full" and not confirm_full(self.screen.ask):
            print(f"Permission stays '{self.settings.permission}'.")
            return
        self.settings.permission = arg
        self.mode.set_permission(arg)
        print(f"Permission is now '{arg}': {PERMISSIONS[arg]}.")

    def set_approval(self, arg: str) -> None:
        if arg not in APPROVALS:
            print("Use /approval with one of:\n" + "\n".join(f"  {k:11} {v}" for k, v in APPROVALS.items()))
            return
        if arg == approvals.ASK and not (self.approver and approvals.install(self.codex, self.approver)):
            print(f"{RED}This Codex engine can't send its requests to the app, so 'ask' isn't available.{RESET}")
            return
        self.settings.approval = arg
        if self.approver:
            self.approver.ask = arg == approvals.ASK
        self.save()
        self.new_conversation(self.mode.name)  # Codex fixes the approval setting when a conversation starts
        print(f"Approval is now '{arg}': {APPROVALS[arg]}. Started a new conversation "
              "(the old one is saved; /resume brings it back).")

    def command(self, line: str) -> bool:
        """Handle a /command. Returns False when the app should quit."""
        name, _, arg = line.partition(" ")
        arg = arg.strip()
        if name in ("/exit", "/quit"):
            return False
        if name == "/help":
            print(HELP)
        elif name in ("/usage", "/cost"):
            print_usage(self.mode, self.extra_usage())
        elif name in ("/clear", "/reset", "/new"):
            self.save()
            self.new_conversation(self.mode.name)
            print("Started a new conversation (the old one is saved; /resume brings it back).")
        elif name == "/mode":
            if arg not in MODES:
                print("Use /mode single or /mode dual")
            elif arg == self.mode.name:
                print(f"Already in {self.describe()}.")
            else:
                self.save()
                self.new_conversation(arg)
                print(f"Switched to {self.describe()} (new conversation).")
        elif name in ("/permission", "/permissions"):
            self.set_permission(arg)
        elif name in ("/approval", "/approvals"):
            self.set_approval(arg)
        elif name == "/model":
            if arg:
                self.set_model(arg)
            else:
                print(f"Now: {self.describe()}\nSwitch with /model <name> [effort]"
                      f"{' (dual mode: /model planner|worker <name> [effort])' if self.mode.name == 'dual' else ''}."
                      f" Efforts: {', '.join(EFFORT_LEVELS)}. Models:")
                self.list_models()
        elif name == "/models":
            self.list_models()
        elif name == "/status":
            self.show_status()
        elif name == "/diff":
            print(git_changes(self.root))
        elif name == "/review":
            self.review(arg)
        elif name == "/mention":
            self.attach(arg, image=False)
        elif name == "/image":
            self.attach(arg, image=True)
        elif name == "/compact":
            try:
                print("Codex is summarising the conversation to free up context." if self.mode.compact()
                      else "Nothing to compact yet.")
            except self.sdk.CodexError as e:
                print(f"{RED}Could not compact: {e}{RESET}")
        elif name == "/fork":
            if self.mode.is_empty():
                print("Nothing to fork yet.")
            else:
                self.save()
                try:
                    self.mode.fork()
                except self.sdk.CodexError as e:
                    print(f"{RED}Could not fork: {e}{RESET}")
                else:
                    self.session_path = sessions.new_session_path(self.mode.name)
                    self.title = f"{self.title or 'conversation'} (fork)"
                    self.save()
                    print("Now in a copy of the conversation. The original is saved; /resume brings it back.")
        elif name == "/rename":
            if not arg:
                print("Use /rename <title>")
            else:
                self.title = arg
                try:
                    self.mode.rename(arg)
                except self.sdk.CodexError:
                    pass  # the saved title is what /sessions shows
                self.save()
                print(f"Renamed to '{arg}'.")
        elif name == "/goal":
            self.goal(arg)
        elif name == "/power":
            self.set_power(arg)
        elif name == "/profile":
            self.set_profile(arg)
        elif name in ("/stop", "/interrupt"):
            print("Nothing is running.")
        elif name == "/undo":
            self.undo()
        elif name == "/history":
            self.history(arg)
        elif name == "/queue":
            self.queue(arg)
        elif name == "/commit":
            self.commit(arg)
        elif name == "/pr":
            self.pull_request(arg)
        elif name == "/save":
            self.save_prefs(arg)
        elif name == "/skills":
            self.show_skills()
        elif name == "/mcp":
            self.show_mcp()
        elif name == "/secrets":
            self.secrets(arg)
        elif name == "/init":
            self.run_task(INIT_TASK)
        elif name == "/login":
            self.login()
        elif name == "/logout":
            try:
                self.codex.logout()
                print("Signed out of Codex. /login or OPENAI_API_KEY signs in again.")
            except self.sdk.CodexError as e:
                print(f"{RED}Could not sign out: {e}{RESET}")
        elif name == "/version":
            print_versions()
        elif name == "/update":
            updated, message = updater.apply()
            print(message)
            engine = updater.engine_check()
            if engine.available:
                # The engine is running inside this app right now, so it can't be replaced from here.
                print(f"{engine.message}\n{YELLOW}To install it: type /exit, then run  py main.py --update{RESET}")
            else:
                print(engine.message)
            if updated:
                print(f"{YELLOW}Type /exit, then start the app again. Your conversation is saved: "
                      f"use --continue to carry on.{RESET}")
        elif name == "/sessions":
            found = sessions.list_for_project(self.root)
            if not found:
                print("No saved conversations for this project yet.")
            for number, (path, data) in enumerate(found, 1):
                print(f"  {number}. {data.get('saved', '?')}  {data['mode']:6}  {data.get('title') or path.name}")
        elif name == "/resume":
            found = [f for f in sessions.list_for_project(self.root) if f[0] != self.session_path]
            index = int(arg) - 1 if arg.isdigit() else 0
            if not found or not 0 <= index < len(found):
                print("Nothing to resume. /sessions lists saved conversations.")
            else:
                self.save()
                self.resume(*found[index])
        else:
            print("Unknown command.\n" + HELP)
        return True

    def loop(self) -> None:
        name, _ = project_instructions(self.root)
        print(f"Codex App {__version__}, {self.describe()}\nProject: {self.root}"
              f"{f' (Codex reads {name})' if name else ' (no AGENTS.md yet: /init writes one)'}\n"
              "Type a task, or /help. Write @path to attach a file.")
        if self.settings.update_check and self.update_notice is None:
            self.update_notice = updater.BackgroundNotice(updater.APP_DIR, APP_HOME)
        self.warn_secrets()
        if self.pump is None:
            self.pump = InputPump(self)
        while True:
            # The update check runs in the background; its notice shows at the first prompt after it's done,
            # never in the middle of what you type.
            notice = self.update_notice.take() if self.update_notice else None
            if notice:
                print(f"{GREEN}* {notice}{RESET}")
            if self.queued:
                line = self.queued.popleft()
                print(f"\n{GREEN}* Next from the queue{f' ({len(self.queued)} more waiting)' if self.queued else ''}: "
                      f"{line}{RESET}")
                if line.startswith("/"):
                    if not self.command(line):
                        break
                else:
                    self.run_task(line)
                continue
            try:
                line = self.pump.get(f"\n{CYAN}you>{RESET} ")
            except KeyboardInterrupt:
                line = None
            if line is None:
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
            else:
                self.run_task(line)
        self.save()
        notice = self.update_notice.take() if self.update_notice else None
        if notice:
            print(f"{GREEN}* {notice}{RESET}")
        print_usage(self.mode, self.extra_usage())


def _value_root(x):
    return getattr(x, "root", x)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        os.system("")  # turns on colour codes in older Windows consoles

    permission_choices = [*PERMISSIONS, *PERMISSION_ALIASES]
    parser = argparse.ArgumentParser(description="OpenAI Codex with single, dual (planner + worker) and team modes.")
    parser.add_argument("task", nargs="*", help="a task to run once; leave out to chat")
    parser.add_argument("--project", "--cd", "-C", dest="project", default=".",
                        help="the project folder Codex works in (default: here)")
    parser.add_argument("--mode", choices=MODES,
                        help="single (default), dual (planner + worker), team (planner + workers side by side)")
    parser.add_argument("--goal", action="store_true",
                        help="treat the task as a goal: Codex works on its own until it is done")
    parser.add_argument("--budget", metavar="TOKENS", help="with --goal: stop after about this many tokens (e.g. 500k)")
    parser.add_argument("--power", choices=profiles.POWER, help="normal (default), high or max")
    parser.add_argument("--profile", choices=profiles.PROFILES, help="auto (default), coding, chat or maths")
    parser.add_argument("--team-size", type=int, help="team mode: how many workers at once (default 3)")
    parser.add_argument("--permission", "--sandbox", "-s", dest="permission", choices=permission_choices,
                        metavar="PERMISSION",
                        help="what Codex may change: plan, workspace (default), full; or read-only, "
                             "workspace-write, danger-full-access")
    parser.add_argument("--approval", "--ask-for-approval", "-a", dest="approval", choices=APPROVALS,
                        help="requests to leave the sandbox: deny_all (default), auto_review, ask")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="carry on with the last conversation in this project")
    parser.add_argument("--model", "-m", help="model for single mode")
    parser.add_argument("--effort", choices=EFFORT_LEVELS, help="reasoning effort for single mode")
    parser.add_argument("--planner-model", help="planner model for dual mode")
    parser.add_argument("--worker-model", help="worker model for dual mode")
    parser.add_argument("--image", "-i", action="append", default=[], metavar="FILE",
                        help="attach an image to the task (can be given more than once)")
    parser.add_argument("--search", action="store_true", help="let Codex search the web live")
    parser.add_argument("--config", "-c", action="append", default=[], metavar="KEY=VALUE",
                        help="a Codex setting, as in official Codex (for example -c model_verbosity=high)")
    parser.add_argument("--update", action="store_true", help="install the newest version from GitHub, then exit")
    parser.add_argument("--version", action="store_true", help="show the version and check for updates, then exit")
    parser.add_argument("--selftest", action="store_true",
                        help="check that the Codex engine still has everything this app uses (no model turn, "
                             "nothing billed), then exit")
    parser.add_argument("--quiet", action="store_true", help="with --selftest: print only what failed")
    args = parser.parse_args(argv)

    if args.version:
        print_versions()
        return 0
    if args.selftest:
        from codex_app import selftest
        return selftest.main(quiet=args.quiet)
    if args.update:
        # Runs before Codex starts, so the engine can be replaced safely.
        _, message, failed = updater.update_everything()
        print(message)
        return 1 if failed else 0

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"{RED}project folder not found: {root}{RESET}")
        return 1
    unsafe = protect.check_project(root)
    if unsafe:
        print(f"{RED}Refused: {unsafe}\nCodex may change anything inside the project folder, so give it the "
              f"folder of one project, for example --project \"{Path.home() / 'projects' / 'my-site'}\".{RESET}")
        return 1
    for kv in args.config:
        if "=" not in kv:
            print(f"{RED}-c needs KEY=VALUE, not '{kv}'.{RESET}")
            return 1
    try:
        settings = Settings.from_env()
        saved = prefs.load(root)  # /save: this project's preferences win over environment variables
        prefs.apply(settings, saved)
        mode_name = args.mode or saved.get("mode") or "single"
        if mode_name not in MODES:
            mode_name = "single"
        if saved:
            print(f"{GREY}Using this project's saved preferences (/save show lists them).{RESET}")
        settings.single_model = args.model or settings.single_model
        settings.single_effort = args.effort or settings.single_effort
        settings.planner_model = args.planner_model or settings.planner_model
        settings.worker_model = args.worker_model or settings.worker_model
        settings.permission = args.permission or settings.permission
        settings.approval = args.approval or settings.approval
        settings.web_search = args.search or settings.web_search
        settings.profile = args.profile or settings.profile
        settings.team_size = args.team_size or settings.team_size
        settings.check()
        start_settings = copy.copy(settings)
        power = args.power or settings.power
        if power != "normal":
            profiles.apply_power(settings, power, start_settings)
    except ValueError as e:
        print(f"{RED}{e}{RESET}")
        return 1
    budget = None
    if args.budget:
        budget = parse_budget(args.budget)
        if budget is None:
            print(f"{RED}--budget needs a number of tokens, for example 500k{RESET}")
            return 1
    screen = Screen()
    if settings.permission == "full" and not confirm_full(screen.ask):
        return 1

    # Loading the Codex SDK takes a few seconds, so the daily update check (GitHub and PyPI) starts first
    # and runs alongside it. Its notice shows at the first prompt once it is ready.
    update_notice = (updater.BackgroundNotice(updater.APP_DIR, APP_HOME)
                     if settings.update_check and not args.task else None)
    try:
        import openai_codex as sdk
    except ImportError:
        print("OpenAI's Codex SDK is not installed. Run:  py -m pip install -r requirements.txt")
        return 1
    images = []
    for image in args.image:
        item = attachment_for(image, root, sdk, image=True)
        if item is None:
            print(f"{RED}image not found: {image}{RESET}")
            return 1
        images.append(item)

    codex_config = sdk.CodexConfig(config_overrides=tuple(args.config)) if args.config else None
    with sdk.Codex(config=codex_config) as codex:
        approver = approvals.Approver(ask=settings.approval == approvals.ASK, ask_user=screen.ask,
                                      notice=lambda text: screen.event("app", "notice", text))
        if not approvals.install(codex, approver) and settings.approval == approvals.ASK:
            print(f"{RED}This Codex engine can't send its requests to the app, so 'ask' isn't available. "
                  f"Using deny_all.{RESET}")
            settings.approval = "deny_all"
        if not sign_in(codex) and args.task:
            return 1
        app = App(codex, sdk, root, settings, mode_name, approver=approver, screen=screen,
                  update_notice=update_notice, start_settings=start_settings)
        approver.ask_user = app.ask  # approval questions go through the input pump once it runs
        if args.resume:
            last = sessions.latest_for_project(root)
            if last:
                app.resume(*last)
            else:
                print("No saved conversation for this project yet; starting a new one.")
        if args.task:
            app.warn_secrets()
        if args.task and args.goal:
            app.pending = images
            app.goal(f"--budget {budget} {' '.join(args.task)}" if budget else " ".join(args.task))
            print_usage(app.mode, app.extra_usage())
            return 0
        if args.task:
            app.run_task(" ".join(args.task), images)
            print_usage(app.mode, app.extra_usage())
            return 0
        app.pending = images
        app.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
