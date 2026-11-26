"""Tests for team mode's separate worktrees (worktrees.py), with real git and a fake Codex: no key, nothing
billed."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import sessions, worktrees  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import TeamMode  # noqa: E402
from test_app import FakeCodex, FakeThread, answer  # noqa: E402

pytestmark = pytest.mark.skipif(not (worktrees.git_version() or (0,)) >= worktrees.MIN_GIT,
                                reason="needs git 2.40 or newer")


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


def git(root, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "core.autocrlf", "false")
    (path / ".gitignore").write_text(".env\n")
    (path / "shared.py").write_text("one = 1\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "start")
    return path


def worktree_list(root) -> list:
    return [line for line in git(root, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


# --- worktrees.py ---------------------------------------------------------------------------------

def test_worktree_starts_from_the_project_as_it_is_now(tmp_path):
    root = repo(tmp_path / "p")
    (root / "shared.py").write_text("one = 'changed, not committed'\n")
    (root / "new.py").write_text("print('new, not added')\n")
    (root / ".env").write_text("SECRET=1\n")
    base = worktrees.base_commit(root)
    tree = worktrees.create(root, base, "worker-1")
    try:
        assert (tree.cwd / "shared.py").read_text() == "one = 'changed, not committed'\n"
        assert (tree.cwd / "new.py").exists()
        assert not (tree.cwd / ".env").exists()  # ignored (secret) files are never copied
        assert git(root, "status", "--porcelain") == " M shared.py\n?? new.py\n"  # the user's state is untouched
        assert git(root, "log", "--oneline").count("\n") == 1  # no commit on the branch
    finally:
        tree.remove()
    assert len(worktree_list(root)) == 1 and not tree.path.exists()


def test_two_workers_changing_one_file_merge_cleanly(tmp_path):
    root = repo(tmp_path / "p")
    base = worktrees.base_commit(root)
    a, b = worktrees.create(root, base, "worker-1"), worktrees.create(root, base, "worker-2")
    try:
        (a.cwd / "shared.py").write_text("one = 'A'\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\n")
        (b.cwd / "shared.py").write_text("one = 1\ntwo = 2\nthree = 3\nfour = 4\nfive = 'B'\n")
        (b.cwd / "added.py").write_text("x = 1\n")
        first = worktrees.merge_back(root, base, a.tree())
        second = worktrees.merge_back(root, base, b.tree())
    finally:
        a.remove()
        b.remove()
    assert first.ok and second.ok
    assert (root / "shared.py").read_text() == "one = 'A'\ntwo = 2\nthree = 3\nfour = 4\nfive = 'B'\n"
    assert (root / "added.py").read_text() == "x = 1\n"
    assert git(root, "diff", "--cached") == ""  # the user's index is untouched


def test_a_conflicting_worker_is_left_out(tmp_path):
    root = repo(tmp_path / "p")
    base = worktrees.base_commit(root)
    a, b = worktrees.create(root, base, "worker-1"), worktrees.create(root, base, "worker-2")
    try:
        (a.cwd / "shared.py").write_text("one = 'A'\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\n")
        (b.cwd / "shared.py").write_text("one = 'B'\ntwo = 2\nthree = 3\nfour = 4\nfive = 5\n")
        assert worktrees.merge_back(root, base, a.tree()).ok
        second = worktrees.merge_back(root, base, b.tree())
    finally:
        a.remove()
        b.remove()
    assert not second.ok and second.conflicts == ["shared.py"]
    assert (root / "shared.py").read_text().startswith("one = 'A'\n")  # nothing half-merged


def test_project_in_a_subfolder_of_the_repository(tmp_path):
    top = repo(tmp_path / "p")
    root = top / "app"
    root.mkdir()
    (root / "main.py").write_text("print(1)\n")
    base = worktrees.base_commit(root)
    tree = worktrees.create(root, base, "worker-1")
    try:
        assert tree.cwd == tree.path / "app" and (tree.cwd / "main.py").exists()
        (tree.cwd / "main.py").write_text("print(2)\n")
        (tree.path / "shared.py").write_text("outside the project folder\n")
        assert "main.py" in tree.diff(tree.tree()) and "shared.py" not in tree.diff(tree.tree())
        assert worktrees.merge_back(root, base, tree.tree()).ok
    finally:
        tree.remove()
    assert (root / "main.py").read_text() == "print(2)\n"
    assert (top / "shared.py").read_text().startswith("one = 1")  # outside the project: not ours to change


def test_not_available_outside_git(tmp_path):
    assert not worktrees.available(tmp_path)


# --- team mode ------------------------------------------------------------------------------------

class WorkingCodex(FakeCodex):
    """A fake Codex whose workers really change files, in the folder each turn runs in."""

    def __init__(self, script, work):
        super().__init__(script)
        self.work = work  # thread id -> function(folder) run when that thread takes a turn

    def thread_start(self, **options):
        thread = super().thread_start(**options)
        return WorkingThread(self, thread.id, options)


class WorkingThread(FakeThread):
    def turn(self, text, **options):
        self.cwd = options.get("cwd", getattr(self, "cwd", self.options["cwd"]))
        if self.id in self.codex.work:
            self.codex.work[self.id](Path(self.cwd))
        return super().turn(text, **options)


def team_plan(*parts):
    return answer(json.dumps({"answer": "Two parts.", "parts": [
        {"title": t, "instructions": f"do {t}", "files": f} for t, f in parts]}))


def verdict():
    return answer(json.dumps({"verdict": "APPROVED", "summary": "ok", "fixes": []}))


def edit(name, old, new):
    def work(folder: Path):
        path = folder / name
        path.write_text(path.read_text().replace(old, new))
    return work


def test_team_workers_get_their_own_worktrees_and_are_merged(tmp_path):
    root = repo(tmp_path / "p")
    codex = WorkingCodex({1: [team_plan(("top", ["shared.py"]), ("bottom", ["other.py"])), verdict()],
                          2: [answer("did top")], 3: [answer("did bottom")]},
                         work={"thr_2": edit("shared.py", "one = 1", "one = 'top'"),
                               "thr_3": edit("shared.py", "five = 5", "five = 'bottom'")})
    events = []
    mode = TeamMode(codex, sdk, root, Settings(team_size=2, fresh_review=False), on_event=lambda a, k, t: events.append(t))
    assert "APPROVED" in mode.run("change both ends")

    worker_turns = [t for t in codex.turns if t["thread"] in ("thr_2", "thr_3")]
    folders = {t["options"]["cwd"] for t in worker_turns}
    assert len(folders) == 2 and str(root) not in folders  # each worker in its own copy
    assert all("sandbox" in t["options"] for t in worker_turns)  # the sandbox follows the folder
    assert all("your own copy of the project" in t["text"] for t in worker_turns)
    assert (root / "shared.py").read_text() == "one = 'top'\ntwo = 2\nthree = 3\nfour = 4\nfive = 'bottom'\n"
    review = codex.turns[-1]["text"]
    assert "one = 'top'" in review and "five = 'bottom'" in review and "NOT MERGED" not in review
    assert all(w.cwd == root for w in mode.workers())  # back in the project folder for fixes
    assert len(worktree_list(root)) == 1 and not any(Path(f).exists() for f in folders)
    assert any("Merged worker-" in e for e in events)


def test_team_conflict_is_reported_to_the_planner(tmp_path):
    root = repo(tmp_path / "p")
    codex = WorkingCodex({1: [team_plan(("a", ["a.py"]), ("b", ["b.py"])), verdict()],
                          2: [answer("did a")], 3: [answer("did b")]},
                         work={"thr_2": edit("shared.py", "one = 1", "one = 'a'"),
                               "thr_3": edit("shared.py", "one = 1", "one = 'b'")})
    mode = TeamMode(codex, sdk, root, Settings(team_size=2, fresh_review=False))
    mode.run("t")
    text = (root / "shared.py").read_text()
    assert text.startswith("one = 'a'") or text.startswith("one = 'b'")  # one merged, never both half-way
    assert "NOT MERGED" in codex.turns[-1]["text"] and "shared.py" in codex.turns[-1]["text"]
    assert len(worktree_list(root)) == 1


def test_one_worker_or_worktrees_off_works_in_the_project_folder(tmp_path):
    root = repo(tmp_path / "p")
    for settings, plan in ((Settings(team_size=1, fresh_review=False), team_plan(("a", ["a.py"]), ("b", ["b.py"]))),
                           (Settings(team_size=2, team_worktrees=False, fresh_review=False), team_plan(("a", ["a.py"]), ("b", ["b.py"])))):
        codex = WorkingCodex({1: [plan, verdict()], 2: [answer("x"), answer("y")], 3: [answer("y")]}, work={})
        TeamMode(codex, sdk, root, settings).run("t")
        assert all("cwd" not in t["options"] for t in codex.turns)
        assert len(worktree_list(root)) == 1
