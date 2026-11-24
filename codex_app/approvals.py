"""Answering Codex's approval requests, and the "ask" approval mode.

When Codex wants to do something its sandbox doesn't allow (run a command outside it, write outside
the project, use the network), it asks. Who answers depends on the approval setting:
- deny_all:    Codex never asks; the request is refused.
- auto_review: Codex's own reviewer answers.
- ask:         you answer y/n in the terminal, like official Codex's "on-request" mode.

Whatever the setting, a request that reaches this app and would damage the system (see protect.py) is
refused without asking.

"ask" needs two things the SDK's simple interface doesn't offer: starting a thread with
approvalsReviewer "user", and a handler for the requests. Both use the SDK's lower-level client
(codex._client), which can change between engine versions; requirements.txt pins the range it was
written for. It is marked experimental for that reason."""

import threading
from typing import Callable, Optional

from . import protect

ASK = "ask"  # stands in for an SDK ApprovalMode value in CodexAgent

COMMAND_APPROVAL = "item/commandExecution/requestApproval"
FILE_APPROVAL = "item/fileChange/requestApproval"

_WIRE_SANDBOX = {"read-only": "read-only", "workspace-write": "workspace-write", "full-access": "danger-full-access"}


def _text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value or "")


class Approver:
    """The handler the SDK calls (on its reader thread) for each request from Codex."""

    def __init__(self, ask: bool = False, ask_user: Optional[Callable[[str], str]] = None,
                 notice: Optional[Callable[[str], None]] = None):
        self.ask = ask
        self.ask_user = ask_user or input
        self.notice = notice or print
        self._lock = threading.Lock()

    def __call__(self, method: str, params: Optional[dict]) -> dict:
        params = params or {}
        if method == COMMAND_APPROVAL:
            return self._command(params)
        if method == FILE_APPROVAL:
            return self._file_change(params)
        return {}  # other requests: same answer as the SDK's own handler

    def _decline(self, why: str) -> dict:
        self.notice(f"Refused: {why}.")
        return {"decision": "decline"}

    def _command(self, params: dict) -> dict:
        command = _text(params.get("command"))
        cwd = params.get("cwd")
        why = protect.dangerous_command(command)
        if why:
            return self._decline(f"the command {why}: {command}")
        if cwd and protect.is_system_path(cwd):
            return self._decline(f"the command would run in a system folder ({cwd})")
        if not self.ask:
            return {"decision": "accept"}
        details = [f"Codex wants to run: {command or '(command not shown)'}"]
        if cwd:
            details.append(f"in: {cwd}")
        if params.get("reason"):
            details.append(f"because: {params['reason']}")
        return self._prompt(details)

    def _file_change(self, params: dict) -> dict:
        root = params.get("grantRoot") or params.get("grant_root")
        if root and protect.is_system_path(root):
            return self._decline(f"Codex asked to write in a system folder ({root})")
        if not self.ask:
            return {"decision": "accept"}
        details = ["Codex wants to change files" + (f" and write in {root}" if root else "") + "."]
        if params.get("reason"):
            details.append(f"because: {params['reason']}")
        return self._prompt(details)

    def _prompt(self, details: list) -> dict:
        with self._lock:  # one question at a time
            for line in details:
                self.notice(line)
            try:
                answer = self.ask_user("Allow it? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
        return {"decision": "accept" if answer in ("y", "yes") else "decline"}


def install(codex, approver: Approver) -> bool:
    """Make the SDK send Codex's requests to this approver. False if this engine version can't."""
    client = getattr(codex, "_client", None)
    if client is None or not hasattr(client, "_approval_handler"):
        return False
    client._approval_handler = approver
    return True


def _params(common: dict) -> dict:
    sandbox = common.get("sandbox")
    params = {"approvalPolicy": "on-request", "approvalsReviewer": "user", "cwd": common.get("cwd"),
              "model": common.get("model"), "developerInstructions": common.get("developer_instructions"),
              "config": common.get("config")}
    if sandbox is not None:
        params["sandbox"] = _WIRE_SANDBOX.get(getattr(sandbox, "value", sandbox), getattr(sandbox, "value", sandbox))
    return {k: v for k, v in params.items() if v is not None}


def open_thread(codex, thread_cls, thread_id: Optional[str] = None, fork: bool = False, **common):
    """Start (or resume, or fork) a thread whose approval requests come to the user."""
    client = codex._client
    if thread_id and fork:
        response = client.thread_fork(thread_id, _params(common))
    elif thread_id:
        response = client.thread_resume(thread_id, _params(common))
    else:
        response = client.thread_start(_params(common))
    return thread_cls(client, response.thread.id)
