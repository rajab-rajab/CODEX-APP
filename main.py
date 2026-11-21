"""Codex App: OpenAI's Codex (github.com/openai/codex) in your terminal, with two modes.

    py main.py --project "C:\\path\\to\\project"                  A) single model mode (default)
    py main.py --project "C:\\path\\to\\project" --mode dual      B) planner + worker mode
    py main.py --project . --continue                            carry on with the last conversation
    py main.py --project . "add a test for the login page"        one task, then exit

Type /help while it runs for the list of commands.
"""

import argparse
import os
import sys

from codex_app import __version__, sessions, updater
from codex_app.config import APP_HOME, APPROVALS, PERMISSIONS, Settings
from codex_app.modes import INIT_TASK, MODES, project_instructions

GREY, CYAN, YELLOW, RED, GREEN, RESET = "\033[90m", "\033[36m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"

HELP = """Commands:
  /mode single | dual     A) one model does everything, B) planner + worker
  /permission <mode>      plan | workspace | full  (what Codex may change)
  /init                   write an AGENTS.md that describes this project
  /models                 list the models Codex can use
  /usage                  tokens used (and cost, if prices are set in config.py)
  /sessions               list saved conversations for this project
  /resume [number]        go back to a saved conversation (default: the most recent)
  /clear                  start a new conversation
  /login                  sign in to Codex with your ChatGPT account
  /update                 install the newest version of Codex App from GitHub
  /version                show this version and check for a newer one
  /exit                   quit
Ctrl+C stops the current task."""


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
        elif kind == "notice":
            print(f"{GREEN}* {text}{RESET}")


def print_usage(mode) -> None:
    for label, u in mode.usage():
        cost = u.cost()
        cost_text = f", about ${cost:.4f}" if cost is not None else ""
        print(f"  {label}: {u.input:,} in ({u.cached:,} cached), {u.output:,} out "
              f"({u.reasoning:,} reasoning){cost_text}")


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
    def __init__(self, codex, sdk, root, settings: Settings, mode_name: str):
        self.codex = codex
        self.sdk = sdk
        self.root = root
        self.settings = settings
        self.screen = Screen()
        self.mode = MODES[mode_name](codex, sdk, root, settings, on_event=self.screen.event)
        self.session_path = sessions.new_session_path(mode_name)
        self.title = ""

    def describe(self) -> str:
        s = self.settings
        mode = (f"single model mode ({s.single_model})" if self.mode.name == "single"
                else f"dual mode: planner {s.planner_model}, worker {s.worker_model}")
        return f"{mode}, permission '{s.permission}'"

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

    def run_task(self, task: str) -> None:
        self.title = self.title or task[:80]
        try:
            self.mode.run(task)
        except KeyboardInterrupt:
            self.mode.repair()  # tells Codex to stop the running turn
            print(f"\n{YELLOW}Stopped. You can carry on with a new message.{RESET}")
        except self.sdk.CodexError as e:
            print(f"{RED}Codex error: {e}{RESET}")
        finally:
            self.screen.finish()
            self.save()
        cost = self.mode.total_cost()
        if cost is not None:
            print(f"{GREY}  (conversation so far: about ${cost:.4f}){RESET}")

    def login(self) -> None:
        handle = self.codex.login_chatgpt_device_code()
        print(f"Open {handle.verification_url} and enter the code {handle.user_code}")
        print("Waiting for you to finish signing in...")
        print("Signed in." if getattr(handle.wait(), "success", False) else "Sign-in did not complete.")

    def command(self, line: str) -> bool:
        """Handle a /command. Returns False when the app should quit."""
        name, _, arg = line.partition(" ")
        arg = arg.strip()
        if name in ("/exit", "/quit"):
            return False
        if name == "/help":
            print(HELP)
        elif name in ("/usage", "/cost"):
            print_usage(self.mode)
        elif name in ("/clear", "/reset"):
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
        elif name == "/permission":
            if arg in PERMISSIONS:
                self.settings.permission = arg
                self.mode.set_permission(arg)
                print(f"Permission is now '{arg}': {PERMISSIONS[arg]}.")
            else:
                print("Use /permission with one of:\n" + "\n".join(f"  {k:9} {v}" for k, v in PERMISSIONS.items()))
        elif name == "/init":
            self.run_task(INIT_TASK)
        elif name == "/models":
            try:
                listing = self.codex.models()
                for m in getattr(listing, "data", None) or []:
                    print(f"  {getattr(m, 'id', None) or getattr(m, 'model', m)}")
            except self.sdk.CodexError as e:
                print(f"{RED}Could not list models: {e}{RESET}")
        elif name == "/login":
            self.login()
        elif name == "/version":
            print(f"Codex App {__version__}")
            print(updater.check().message)
        elif name == "/update":
            updated, message = updater.apply()
            print(message)
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
              "Type a task, or /help.")
        if self.settings.update_check:
            notice = updater.due_notice(updater.APP_DIR, APP_HOME)
            if notice:
                print(f"{GREEN}* {notice}{RESET}")
        while True:
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
        print_usage(self.mode)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        os.system("")  # turns on colour codes in older Windows consoles

    parser = argparse.ArgumentParser(description="OpenAI Codex with single and dual (planner + worker) modes.")
    parser.add_argument("task", nargs="*", help="a task to run once; leave out to chat")
    parser.add_argument("--project", default=".", help="the project folder Codex works in (default: here)")
    parser.add_argument("--mode", choices=MODES, default="single")
    parser.add_argument("--permission", choices=PERMISSIONS, help="what Codex may change (default: workspace)")
    parser.add_argument("--approval", choices=APPROVALS, help="requests to leave the sandbox (default: deny_all)")
    parser.add_argument("-c", "--continue", dest="resume", action="store_true",
                        help="carry on with the last conversation in this project")
    parser.add_argument("--model", help="model for single mode")
    parser.add_argument("--planner-model", help="planner model for dual mode")
    parser.add_argument("--worker-model", help="worker model for dual mode")
    parser.add_argument("--update", action="store_true", help="install the newest version from GitHub, then exit")
    parser.add_argument("--version", action="store_true", help="show the version and check for updates, then exit")
    args = parser.parse_args(argv)

    if args.version:
        print(f"Codex App {__version__}")
        print(updater.check().message)
        return 0
    if args.update:
        updated, message = updater.apply()
        print(message)
        return 0 if updated or "up to date" in message else 1

    try:
        import openai_codex as sdk
    except ImportError:
        print("OpenAI's Codex SDK is not installed. Run:  py -m pip install -r requirements.txt")
        return 1

    from pathlib import Path
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"{RED}project folder not found: {root}{RESET}")
        return 1
    try:
        settings = Settings.from_env()
        settings.single_model = args.model or settings.single_model
        settings.planner_model = args.planner_model or settings.planner_model
        settings.worker_model = args.worker_model or settings.worker_model
        settings.permission = args.permission or settings.permission
        settings.approval = args.approval or settings.approval
        settings.check()
    except ValueError as e:
        print(f"{RED}{e}{RESET}")
        return 1

    with sdk.Codex() as codex:
        if not sign_in(codex) and args.task:
            return 1
        app = App(codex, sdk, root, settings, args.mode)
        if args.resume:
            last = sessions.latest_for_project(root)
            if last:
                app.resume(*last)
            else:
                print("No saved conversation for this project yet; starting a new one.")
        if args.task:
            app.run_task(" ".join(args.task))
            print_usage(app.mode)
            return 0
        app.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
