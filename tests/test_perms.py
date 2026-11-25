"""Tests for giving back files Codex's Windows sandbox made unreadable (perms.py). Real unreadable files can only
be made by the sandbox itself, so these use a fake: nothing is run in a sandbox and nothing is billed."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import perms, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex, answer  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


@pytest.fixture
def locked(tmp_path, monkeypatch):
    """A project where some files and a folder can't be opened, as the sandbox leaves them."""
    for rel in ("app.py", "ledger/report.py", "tests/test_new.py", ".pytest_cache/v/x", "node_modules/pkg/a.js"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    state = {"files": {"ledger/report.py", "tests/test_new.py", "node_modules/pkg/a.js"}, "dirs": {".pytest_cache"}}
    rel = lambda p: Path(p).resolve().relative_to(tmp_path.resolve()).as_posix()  # noqa: E731
    monkeypatch.setattr(perms, "applies", lambda: True)
    monkeypatch.setattr(perms, "_opens", lambda p: rel(p) not in state["files"])
    real_scandir = os.scandir

    def scandir(folder):
        if rel(folder) in state["dirs"]:
            raise PermissionError(folder)
        return real_scandir(folder)
    monkeypatch.setattr(perms.os, "scandir", scandir)
    monkeypatch.setattr(perms, "codex_exe", lambda: r"C:\codex\codex.exe")
    monkeypatch.setattr(perms, "current_user", lambda: r"PC\me")
    return tmp_path, state


def fake_sandbox(state, fixes=True):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if fixes:
            for p in cmd[cmd.index("--") + 5:]:
                state["files"].discard(p.replace("\\", "/"))
                state["dirs"].discard(p.replace("\\", "/"))
    run.calls = calls
    return run


def test_finds_what_this_account_cant_open(locked):
    root, _ = locked
    # a folder that can't be listed is reported itself; big dependency folders aren't looked into
    assert perms.find_unreadable(root) == [".pytest_cache", "ledger/report.py", "tests/test_new.py"]


def test_nothing_to_do_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(perms, "applies", lambda: False)
    assert perms.find_unreadable(tmp_path) == [] and perms.check_and_repair(tmp_path) == ([], [])


def test_the_sandbox_gives_access_back(locked):
    root, state = locked
    run = fake_sandbox(state)
    fixed, left = perms.check_and_repair(root, run=run)
    assert fixed == [".pytest_cache", "ledger/report.py", "tests/test_new.py"] and left == []
    [(cmd, kw)] = run.calls
    assert cmd[:5] == [r"C:\codex\codex.exe", "sandbox", "-c", 'sandbox_mode="workspace-write"', "--"]
    assert cmd[5] == sys.executable and cmd[6] == "-c" and "icacls" in cmd[7] and "/grant" in cmd[7]
    assert cmd[8] == r"PC\me" and cmd[9:] == [".pytest_cache", r"ledger\report.py", r"tests\test_new.py"]
    assert kw["cwd"] == str(root)  # the sandbox may only change files in the project


def test_what_couldnt_be_fixed_is_reported(locked):
    root, state = locked
    fixed, left = perms.check_and_repair(root, run=fake_sandbox(state, fixes=False))
    assert fixed == [] and left == [".pytest_cache", "ledger/report.py", "tests/test_new.py"]
    assert "takeown" in perms.manual_fix(root, left) and "icacls" in perms.manual_fix(root, left)


def test_many_files_go_in_several_calls(locked, monkeypatch):
    root, state = locked
    paths = [f"f{n}.txt" for n in range(perms.CHUNK * 2 + 5)]
    state["files"] |= set(paths)
    for p in paths:
        (root / p).write_text("x")
    run = fake_sandbox(state)
    fixed, left = perms.repair(root, paths, run=run)
    assert len(run.calls) == 3 and left == [] and len(fixed) == len(paths)


def test_no_engine_means_no_repair(locked, monkeypatch):
    root, _ = locked
    monkeypatch.setattr(perms, "codex_exe", lambda: None)
    assert perms.repair(root, ["ledger/report.py"]) == ([], ["ledger/report.py"])


def make_app(root, **settings):
    import main
    return main.App(FakeCodex({1: [answer("done")]}), sdk, root, Settings(**settings), "single")


def test_the_app_repairs_after_each_turn(locked, monkeypatch, capsys):
    root, state = locked
    run = fake_sandbox(state)
    monkeypatch.setattr(perms, "check_and_repair", lambda r, run=run: perms.repair(r, perms.find_unreadable(r), run))
    app = make_app(root)
    app.run_task("x")
    out = capsys.readouterr().out
    assert "had left 3 file(s) you couldn't open" in out and "access again" in out and run.calls


def test_the_app_says_what_it_couldnt_fix(locked, monkeypatch, capsys):
    root, state = locked
    monkeypatch.setattr(perms, "check_and_repair", lambda r: ([], ["ledger/report.py"]))
    make_app(root).run_task("x")
    out = capsys.readouterr().out
    assert "Still unreadable for your account" in out and "ledger/report.py" in out and "takeown" in out


def test_it_can_be_turned_off(locked, monkeypatch, capsys):
    root, _ = locked
    monkeypatch.setattr(perms, "check_and_repair", lambda r: pytest.fail("checked"))
    make_app(root, fix_permissions=False).run_task("x")


def test_team_mode_repairs_a_worktree_before_merging(tmp_path, monkeypatch):
    from codex_app.modes import TeamMode
    checked, notices = [], []
    monkeypatch.setattr(perms, "applies", lambda: True)
    monkeypatch.setattr(perms, "check_and_repair", lambda folder: (checked.append(folder) or ([], ["a.py"])))
    mode = TeamMode(FakeCodex({}), sdk, tmp_path, Settings(), on_event=lambda a, k, t: notices.append(t))
    mode._fix_permissions(tmp_path / "wt")
    assert checked == [tmp_path / "wt"] and any("may not be merged" in n for n in notices)
