"""Separate git worktrees for team-mode workers, the way official Codex keeps parallel work apart.

Each worker that works at the same time as others gets its own temporary copy of the project: a git
worktree made from a snapshot of the project as it is now, uncommitted changes and new files included
(files that .gitignore leaves out, such as .env, node_modules or build output, are not copied). Workers
then never see each other's half-written files or test runs.

When the workers are done, each part's changes are merged back into your project folder with git's
three-way merge (base snapshot, your folder now, the worker's copy), one part after another. Parts that
change different lines of the same file merge cleanly; a part that conflicts is left out and reported, so
the planner can have it redone. Your branches, index, stash and commits are never touched: the snapshot
and merge use git objects and a temporary index only (see undo.py), and the worktrees are removed after.

Needs git 2.40 or newer (for `git merge-tree --merge-base`). Without it, or outside a git project, team
mode works in the project folder as before."""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import undo
from .undo import _git, _TempIndex, git_top

MIN_GIT = (2, 40)
_IDENTITY = {"GIT_AUTHOR_NAME": "Codex App", "GIT_AUTHOR_EMAIL": "codex-app@localhost",
             "GIT_COMMITTER_NAME": "Codex App", "GIT_COMMITTER_EMAIL": "codex-app@localhost"}


def git_version() -> Optional[tuple]:
    try:
        done = _git(Path.cwd(), "--version")
    except Exception:  # git missing, or it hung
        return None
    found = re.search(r"(\d+)\.(\d+)", done.stdout or "")
    return (int(found[1]), int(found[2])) if done.returncode == 0 and found else None


def available(root: Path) -> bool:
    """True if team workers can get their own worktrees here: a git project and a new enough git."""
    version = git_version()
    return bool(version and version >= MIN_GIT and git_top(root) is not None)


def _rel(root: Path, top: Path) -> str:
    rel = Path(root).resolve().relative_to(top.resolve()).as_posix()
    return "" if rel == "." else rel


@dataclass
class Worktree:
    top: Path          # the main repository's top folder
    path: Path         # the worktree's top folder
    cwd: Path          # where the worker works: the project folder inside the worktree
    base: str          # the snapshot commit the worktree started from
    rel: str = ""      # the project folder, relative to the repository's top ("" for the top itself)
    _holder: Optional[Path] = field(default=None, repr=False)

    def tree(self) -> Optional[str]:
        """A git tree id of the worker's files now (what .gitignore leaves out is not included)."""
        with _TempIndex() as index:
            start = _git(self.path, "read-tree", self.base, env=index.env)
            added = _git(self.path, "add", "-A", "--", ".", env=index.env)
            tree = _git(self.path, "write-tree", env=index.env)
        if start.returncode or added.returncode or tree.returncode:
            return None
        return tree.stdout.strip() or None

    def diff(self, tree: Optional[str], since: Optional[str] = None) -> str:
        """The worker's changes inside the project folder, as a diff from `since` (a tree or commit; the
        base snapshot by default)."""
        if not tree:
            return ""
        done = _git(self.top, "diff", "--no-color", "--no-ext-diff", since or self.base, tree,
                    "--", *([self.rel] if self.rel else []))
        return done.stdout if done.returncode == 0 else ""

    def remove(self) -> None:
        """Delete the worktree and git's record of it (always safe to call again)."""
        _git(self.top, "worktree", "remove", "--force", str(self.path))
        if self._holder is not None:
            shutil.rmtree(self._holder, ignore_errors=True)
        _git(self.top, "worktree", "prune")


def base_commit(root: Path) -> Optional[str]:
    """A commit holding the project as it is now, uncommitted changes and new files included. It is
    on no branch, so it never shows up in `git log` and git cleans it up by itself later."""
    tree = undo.snapshot(root)
    top = git_top(root)
    if not tree or top is None:
        return None
    done = _git(top, "commit-tree", tree, "-m", "Codex App: team snapshot", env={**os.environ, **_IDENTITY})
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def base_tree(tree: Worktree) -> Optional[str]:
    """The tree id of the worktree's base snapshot (to tell whether a worker changed anything)."""
    done = _git(tree.top, "rev-parse", f"{tree.base}^{{tree}}")
    return done.stdout.strip() if done.returncode == 0 else None


def create(root: Path, base: str, name: str) -> Worktree:
    """A new worktree for one worker, from the base commit. Raises RuntimeError if git refuses."""
    top = git_top(root)
    if top is None:
        raise RuntimeError("not a git project")
    holder = Path(tempfile.mkdtemp(prefix=f"codex-app-{name}-"))
    path = holder / "project"
    done = _git(top, "worktree", "add", "--detach", "--quiet", str(path), base)
    if done.returncode:
        shutil.rmtree(holder, ignore_errors=True)
        raise RuntimeError(done.stderr.strip() or "git worktree add failed")
    rel = _rel(root, top)
    return Worktree(top=top, path=path, cwd=path / rel if rel else path, base=base, rel=rel, _holder=holder)


@dataclass
class Merged:
    ok: bool
    files: list         # [(what, path)] changed in the project folder (see undo.changes)
    conflicts: list     # paths that conflicted (when not ok)
    error: str = ""


def _conflicts(output: str) -> list:
    """The conflicted paths from `git merge-tree --write-tree --name-only` output."""
    lines = output.splitlines()[1:]  # the first line is the (conflicted) tree id
    paths = []
    for line in lines:
        if not line.strip():
            break  # the informational messages come after an empty line
        paths.append(line.strip())
    return paths


def merge_back(root: Path, base: str, tree: str) -> Merged:
    """Merge a worker's files (tree) into the project folder: a three-way merge of the base snapshot,
    the folder as it is now and the worker's copy. Nothing changes if it conflicts."""
    top = git_top(root)
    now = undo.snapshot(root)
    if top is None or now is None:
        return Merged(False, [], [], "the project's files couldn't be read with git")
    if now == tree:
        return Merged(True, [], [])
    ours = _git(top, "commit-tree", now, "-m", "Codex App: project now", env={**os.environ, **_IDENTITY})
    theirs = _git(top, "commit-tree", tree, "-m", "Codex App: worker", env={**os.environ, **_IDENTITY})
    if ours.returncode or theirs.returncode:
        return Merged(False, [], [], (ours.stderr or theirs.stderr).strip() or "git commit-tree failed")
    done = _git(top, "merge-tree", "--write-tree", "--name-only", "--no-messages", f"--merge-base={base}",
                ours.stdout.strip(), theirs.stdout.strip())
    if done.returncode == 1:
        return Merged(False, [], _conflicts(done.stdout), "conflict")
    if done.returncode:
        return Merged(False, [], [], done.stderr.strip() or "git merge-tree failed")
    merged = done.stdout.splitlines()[0].strip()
    try:
        files = undo.restore(root, merged)
    except RuntimeError as e:
        return Merged(False, [], [], str(e))
    return Merged(True, files, [])


def cleanup_stale(root: Path) -> None:
    """Forget worktrees whose folders are gone (for example after the app was closed mid-task)."""
    top = git_top(root)
    if top is not None:
        _git(top, "worktree", "prune")
