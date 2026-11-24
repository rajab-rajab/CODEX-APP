"""Putting the project's files back the way they were before a message (for /undo).

In a git project, a snapshot of every file (except those .gitignore leaves out) is taken before each
message, the way official Codex's undo works. The snapshot is a git tree object written with a
temporary index, so your own index, branches, stash and commits are never touched, and nothing shows
up in `git status` or `git log`. Undoing restores exactly the files that differ from the snapshot.

Files that .gitignore leaves out (for example .env, node_modules, build output) are never part of a
snapshot, so /undo never changes or deletes them.

Without git, the app falls back to reversing the changes Codex reported for each turn (its diffs)."""

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _git(root: Path, *args, env=None, input_text=None) -> subprocess.CompletedProcess:
    # Never let git inherit the app's input: on Windows a child process can hang at start while the
    # steering thread is reading that same input. input="" gives git its own, empty one.
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, input=input_text if input_text is not None else "", timeout=120)


def git_top(root: Path) -> Optional[Path]:
    """The git repository's top folder, or None if the project isn't in one (or git is missing)."""
    try:
        done = _git(root, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


class _TempIndex:
    """A throwaway git index, so snapshots never touch the user's own index."""

    def __enter__(self):
        handle, self.path = tempfile.mkstemp(prefix="codex-app-index-")
        os.close(handle)
        os.remove(self.path)  # git creates it; an empty file isn't a valid index
        self.env = {**os.environ, "GIT_INDEX_FILE": self.path}
        return self

    def __exit__(self, *exc):
        for path in (self.path, self.path + ".lock"):
            try:
                os.remove(path)
            except OSError:
                pass


def snapshot(root: Path) -> Optional[str]:
    """A git tree id holding every file of the repository as it is now, or None without git."""
    top = git_top(root)
    if top is None:
        return None
    with _TempIndex() as index:
        has_head = _git(top, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
        start = _git(top, "read-tree", "HEAD" if has_head else "--empty", env=index.env)
        added = _git(top, "add", "-A", "--", ".", env=index.env)
        tree = _git(top, "write-tree", env=index.env)
    if start.returncode or added.returncode or tree.returncode:
        return None
    return tree.stdout.strip() or None


def changes(root: Path, tree: str) -> list[tuple[str, str]]:
    """What undoing to `tree` would do: [(what, path)] with what = 'restore', 'delete' (made after the
    snapshot) or 'bring back' (deleted after it). Paths are relative to the project folder."""
    top = git_top(root)
    now = snapshot(root)
    if top is None or now is None or now == tree:
        return []
    done = _git(top, "diff-tree", "-r", "-z", "--no-renames", "--name-status", tree, now)
    fields = [f for f in done.stdout.split("\0") if f]
    out = []
    prefix = Path(root).resolve().relative_to(top.resolve()).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    for status, path in zip(fields[0::2], fields[1::2]):
        if prefix and not path.startswith(prefix):
            continue  # outside the project folder: not ours to change
        what = {"A": "delete", "D": "bring back"}.get(status[:1], "restore")
        out.append((what, path[len(prefix):]))
    return out


def restore(root: Path, tree: str) -> list[tuple[str, str]]:
    """Put the project's files back as they were in `tree`. Returns what was done (see changes())."""
    todo = changes(root, tree)
    if not todo:
        return []
    top = git_top(root)
    root = Path(root).resolve()
    rel = root.relative_to(top.resolve()).as_posix()
    prefix = "" if rel == "." else rel + "/"
    for what, path in todo:
        if what == "delete":
            try:
                (root / path).unlink()
            except FileNotFoundError:
                pass
    back = [prefix + path for what, path in todo if what != "delete"]
    if back:
        with _TempIndex() as index:
            _git(top, "read-tree", tree, env=index.env)
            done = _git(top, "checkout-index", "-f", "-z", "--stdin", env=index.env,
                        input_text="\0".join(back) + "\0")
        if done.returncode:
            raise RuntimeError(done.stderr.strip() or "git checkout-index failed")
    return todo


def reverse_diffs(root: Path, diffs: list[str]) -> list[str]:
    """Without git history: undo Codex's reported diffs, newest first. All or nothing: if any no longer
    fits the files (they were changed since), nothing is changed. Returns the files touched."""
    diffs = [d if d.endswith("\n") else d + "\n" for d in diffs if d and d.strip()]
    if not diffs:
        return []
    patch = "".join(reversed(diffs))

    def apply(*extra):
        return subprocess.run(["git", "apply", "-R", "--unsafe-paths", *extra, "-"], cwd=str(root), input=patch,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    try:
        check = apply("--check")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"git is needed to undo file changes: {e}")
    if check.returncode:
        raise RuntimeError("the files were changed since, so the changes can't be reversed safely: "
                           + (check.stderr.strip().splitlines() or ["unknown reason"])[0])
    stat = apply("--numstat")
    done = apply()
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "git apply failed")
    return [line.split("\t")[-1] for line in stat.stdout.splitlines() if "\t" in line]
