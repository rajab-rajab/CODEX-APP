"""Tests for keeping Codex away from the system, and for answering Codex's approval requests."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_app import approvals, protect  # noqa: E402


# --- project folders ------------------------------------------------------------------------------

def test_disk_roots_and_system_folders_are_refused(tmp_path):
    assert "root of a disk" in protect.check_project(Path(tmp_path).anchor)
    system = protect.system_dirs()[0]
    assert "system folder" in protect.check_project(system)
    assert "system folder" in protect.check_project(os.path.join(system, "anything"))


def test_home_folder_and_its_parents_are_refused(tmp_path):
    home = tmp_path / "home" / "me"
    assert "whole home folder" in protect.check_project(home, home=home)
    assert "contains it" in protect.check_project(tmp_path / "home", home=home)
    assert protect.check_project(home / "projects" / "site", home=home) is None


def test_a_normal_project_is_allowed(tmp_path):
    assert protect.check_project(tmp_path / "site", home=tmp_path / "elsewhere") is None
    assert not protect.is_system_path(tmp_path / "site")
    assert protect.is_system_path(protect.system_dirs()[0])


# --- commands -------------------------------------------------------------------------------------

def test_dangerous_commands():
    for command in ["format C: /q", "diskpart", "bcdedit /set safeboot", r"reg delete HKLM\Software\X /f",
                    "Remove-ItemProperty -Path HKLM:\\Software\\X -Name y", "rm -rf /", "sudo rm -rf ~",
                    "mkfs.ext4 /dev/sda1", "dd if=x of=/dev/sda", "shutdown /s", "vssadmin delete shadows /all",
                    "chmod -R 777 /", ["rd", "/s", "/q", "C:\\"]]:
        assert protect.dangerous_command(command), command


def test_changing_a_system_folder_is_refused():
    system = protect.system_dirs()[0]
    assert "system folder" in protect.dangerous_command(f'del /q "{system}\\x.dll"')
    assert protect.dangerous_command(f'dir "{system}"') is None  # only reading it


def test_normal_commands_are_fine():
    for command in ["py -m pytest -q", "rm -rf build", "git status", "npm install", "del /q build\\*.tmp",
                    "rmdir /s /q dist", "echo formatting done"]:
        assert protect.dangerous_command(command) is None, command


# --- approval requests ----------------------------------------------------------------------------

def approver(answer="", ask=True):
    said = []
    return approvals.Approver(ask=ask, ask_user=lambda q: answer, notice=said.append), said


def test_without_ask_requests_are_accepted_but_dangerous_ones_refused():
    a, said = approver(ask=False)
    assert a(approvals.COMMAND_APPROVAL, {"command": "py -m pytest", "cwd": "/home/me/site"}) == {"decision": "accept"}
    assert a(approvals.COMMAND_APPROVAL, {"command": ["diskpart"]}) == {"decision": "decline"}
    assert "Refused" in said[-1]


def test_ask_mode_asks_the_user():
    assert approver("y")[0](approvals.COMMAND_APPROVAL, {"command": "npm install"}) == {"decision": "accept"}
    assert approver("n")[0](approvals.COMMAND_APPROVAL, {"command": "npm install"}) == {"decision": "decline"}
    assert approver("")[0](approvals.FILE_APPROVAL, {"reason": "edit"}) == {"decision": "decline"}
    a, said = approver("yes")
    assert a(approvals.FILE_APPROVAL, {"grantRoot": "/home/me/other"}) == {"decision": "accept"}
    assert any("/home/me/other" in line for line in said)


def test_system_folders_are_refused_without_asking():
    a, said = approver("y")
    system = protect.system_dirs()[0]
    assert a(approvals.FILE_APPROVAL, {"grantRoot": system}) == {"decision": "decline"}
    assert a(approvals.COMMAND_APPROVAL, {"command": "ls", "cwd": system}) == {"decision": "decline"}


def test_other_requests_get_the_sdk_default():
    assert approver("y")[0]("item/tool/requestUserInput", {}) == {}


def test_install():
    a, _ = approver()
    codex = NS(_client=NS(_approval_handler=None))
    assert approvals.install(codex, a) and codex._client._approval_handler is a
    assert approvals.install(NS(), a) is False


class FakeClient:
    def __init__(self):
        self.calls = []

    def _reply(self, method, thread_id, params):
        self.calls.append((method, thread_id, params))
        return NS(thread=NS(id="thr_new"))

    def thread_start(self, params):
        return self._reply("start", None, params)

    def thread_resume(self, thread_id, params):
        return self._reply("resume", thread_id, params)

    def thread_fork(self, thread_id, params):
        return self._reply("fork", thread_id, params)


def test_open_thread_asks_codex_to_send_requests_to_the_user():
    client = FakeClient()
    codex = NS(_client=client)
    sandbox = NS(value="full-access")
    thread = approvals.open_thread(codex, lambda c, i: (c, i), cwd="/p", model="m", sandbox=sandbox,
                                   developer_instructions="rules", config={"web_search": "live"})
    assert thread == (client, "thr_new")
    method, _, params = client.calls[0]
    assert method == "start" and params["approvalPolicy"] == "on-request" and params["approvalsReviewer"] == "user"
    assert params["sandbox"] == "danger-full-access" and params["developerInstructions"] == "rules"
    approvals.open_thread(codex, lambda c, i: i, "thr_1", cwd="/p")
    approvals.open_thread(codex, lambda c, i: i, "thr_1", fork=True, cwd="/p")
    assert [(m, t) for m, t, _ in client.calls[1:]] == [("resume", "thr_1"), ("fork", "thr_1")]
