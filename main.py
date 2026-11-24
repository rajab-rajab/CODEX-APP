"""Codex App: OpenAI's Codex (github.com/openai/codex) in your terminal, with two modes.

    py main.py --project "C:\\path\\to\\project"                  A) single model mode (default)
    py main.py --project "C:\\path\\to\\project" --mode dual      B) planner + worker mode
    py main.py --project . --continue                            carry on with the last conversation
    py main.py --project . "add a test for the login page"        one task, then exit

Type /help while it runs for the list of commands.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from codex_app import __version__, approvals, protect, sessions, updater
from codex_app.config import APP_HOME, APPROVALS, EFFORT_LEVELS, PERMISSION_ALIASES, PERMISSIONS, Settings, \
    permission_name
from codex_app.modes import INIT_TASK, MODES, make_reviewer, project_instructions, review_task

GREY, CYAN, YELLOW, RED, GREEN, RESET = "\033[90m", "\033[36m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
DIFF_LINES_SHOWN = 80

HELP = """Commands (most are the same as official Codex's):
  /mode single | dual     A) one model does everything, B) planner + worker
  /model [name [effort]]  show or switch the model and reasoning effort
                          (dual mode: /model planner|worker <name> [effort])
  /permission <mode>      plan | workspace | full  (official names work too: read-only,
                          workspace-write, danger-full-access)
  /approval <mode>        deny_all | auto_review | ask  (what happens when Codex wants to leave
                          the sandbox; starts a new conversation)
  /status                 models, permission, account, versions, tokens and context left
  /diff                   the project's git changes, including new files
  /review [what]          a read-only code review: uncommitted (default), branch <base>,
                          commit <sha>, or your own instructions
  /mention <file>         attach a file to your next message (or write @path in a message)
  /image <file>           attach an image to your next message
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
Ctrl+C stops the current task."""


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

    def finish(self):
        if self.streaming_from:
            print()
            self.streaming_from = None

    def event(self, agent: str, kind: str, text: str) -> None:
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
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
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
                 update_notice=None):
        self.update_notice = update_notice  # an updater.BackgroundNotice started before Codex, or None
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
        mode = (f"single model mode ({s.single_model}, {s.single_effort})" if self.mode.name == "single"
                else f"dual mode: planner {s.planner_model} ({s.planner_effort}), "
                     f"worker {s.worker_model} ({s.worker_effort})")
        return f"{mode}, permission '{s.permission}', approval '{s.approval}'"

    def new_conversation(self, mode_name: str) -> None:
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
        if left is not None and left <= 15:
            print(f"{YELLOW}  Context is nearly full: /compact frees it up, /new starts fresh.{RESET}")

    def run_task(self, task: str, attachments=()) -> None:
        self.title = self.title or task[:80]
        attachments = [*self.pending, *attachments, *mentions_in(task, self.root, self.sdk)]
        self.pending = []
        try:
            self.mode.run(task, attachments) if attachments else self.mode.run(task)
        except KeyboardInterrupt:
            self.mode.repair()  # tells Codex to stop the running turn
            print(f"\n{YELLOW}Stopped. You can carry on with a new message.{RESET}")
        except self.sdk.CodexError as e:
            print(f"{RED}Codex error: {e}{RESET}")
        finally:
            self.screen.finish()
            self.save()
        self.status_line()

    def login(self) -> None:
        handle = self.codex.login_chatgpt_device_code()
        print(f"Open {handle.verification_url} and enter the code {handle.user_code}")
        print("Waiting for you to finish signing in...")
        print("Signed in." if getattr(handle.wait(), "success", False) else "Sign-in did not complete.")

    # --- commands ------------------------------------------------------------------------------

    def set_model(self, arg: str) -> None:
        words = arg.split()
        role = None
        if self.mode.name == "dual":
            if not words or words[0] not in ("planner", "worker"):
                print("In dual mode: /model planner <name> [effort]  or  /model worker <name> [effort]")
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
            agent = self.mode.worker
        agent.set_model(model, effort)
        print(f"{agent.name} now uses {agent.model} ({agent.effort}), from the next message on.")

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
        print(f"  Saved as:    {self.session_path.name}{f'  ({self.title})' if self.title else ''}")
        for agent in self.mode.agents():
            print(f"  {agent.name:11}  thread {agent.thread_id or '(not started)'}")
        print_usage(self.mode, self.extra_usage())

    def extra_usage(self):
        return [(f"reviewer ({self.reviewer.model})", self.reviewer.usage)] if self.reviewer else []

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
        model = self.settings.planner_model if self.mode.name == "dual" else self.settings.single_model
        if self.reviewer is None or self.reviewer.model != model:
            self.reviewer = make_reviewer(self.codex, self.sdk, self.root, self.settings, model,
                                          on_event=self.screen.event)
        self.reviewer.reset()  # each review is a fresh, read-only conversation
        print(f"{GREEN}* Reviewing (read-only, {model}).{RESET}")
        try:
            self.reviewer.send(task)
        except KeyboardInterrupt:
            self.reviewer.interrupt()
            print(f"\n{YELLOW}Review stopped.{RESET}")
        except self.sdk.CodexError as e:
            print(f"{RED}Codex error: {e}{RESET}")
        finally:
            self.screen.finish()

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
        while True:
            # The update check runs in the background; its notice shows at the first prompt after it's done,
            # never in the middle of what you type.
            notice = self.update_notice.take() if self.update_notice else None
            if notice:
                print(f"{GREEN}* {notice}{RESET}")
            try:
                line = input(f"\n{CYAN}you>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
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
    parser = argparse.ArgumentParser(description="OpenAI Codex with single and dual (planner + worker) modes.")
    parser.add_argument("task", nargs="*", help="a task to run once; leave out to chat")
    parser.add_argument("--project", "--cd", "-C", dest="project", default=".",
                        help="the project folder Codex works in (default: here)")
    parser.add_argument("--mode", choices=MODES, default="single")
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
    args = parser.parse_args(argv)

    if args.version:
        print_versions()
        return 0
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
        settings.single_model = args.model or settings.single_model
        settings.single_effort = args.effort or settings.single_effort
        settings.planner_model = args.planner_model or settings.planner_model
        settings.worker_model = args.worker_model or settings.worker_model
        settings.permission = args.permission or settings.permission
        settings.approval = args.approval or settings.approval
        settings.web_search = args.search or settings.web_search
        settings.check()
    except ValueError as e:
        print(f"{RED}{e}{RESET}")
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
        app = App(codex, sdk, root, settings, args.mode, approver=approver, screen=screen,
                  update_notice=update_notice)
        if args.resume:
            last = sessions.latest_for_project(root)
            if last:
                app.resume(*last)
            else:
                print("No saved conversation for this project yet; starting a new one.")
        if args.task:
            app.run_task(" ".join(args.task), images)
            print_usage(app.mode, app.extra_usage())
            return 0
        app.pending = images
        app.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
