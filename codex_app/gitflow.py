"""/commit and /pr: committing Codex's work and opening a pull request.

Everything here runs git (and GitHub's `gh` for pull requests) as you, in the project folder, and only
after you said yes in the app. Git hooks run as they normally do. Secret files (see protect.py) are never
staged, even when .gitignore doesn't exclude them."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import protect

DIFF_LIMIT = 30_000  # characters of the diff given to Codex to write a message


class GitError(RuntimeError):
    pass


def _run(root: Path, *cmd, check: bool = True, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    # input="" rather than inheriting: on Windows a child that inherits the app's input can hang (see undo.py).
    done = subprocess.run(list(cmd), cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", input=input_text if input_text is not None else "", timeout=300)
    if check and done.returncode:
        raise GitError((done.stderr or done.stdout).strip() or f"{cmd[0]} failed")
    return done


def git(root: Path, *args, check: bool = True, input_text: Optional[str] = None) -> str:
    return _run(root, "git", *args, check=check, input_text=input_text).stdout


def is_repo(root: Path) -> bool:
    try:
        return _run(root, "git", "rev-parse", "--is-inside-work-tree", check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def top(root: Path) -> Path:
    """The repository's top folder. Paths below are relative to it, as git reports them."""
    return Path(git(root, "rev-parse", "--show-toplevel").strip())


def _scope(root: Path) -> str:
    rel = Path(root).resolve().relative_to(top(root).resolve()).as_posix()
    return rel or "."


def changed_files(root: Path) -> list[tuple[str, str]]:
    """[(status, path)] of everything not committed in the project folder, new files included. Paths are
    relative to the repository's top folder."""
    out = git(top(root), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", _scope(root))
    entries, fields, i = [], out.split("\0"), 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC":  # a rename: the old name follows
            i += 1
        entries.append((status.strip() or "M", path))
    return entries


def split_secrets(files: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """(paths to commit, secret paths that must not be committed)."""
    keep, secret = [], []
    for _, path in files:
        (secret if protect.is_secret_path(path) else keep).append(path)
    return keep, secret


def diff_text(root: Path, paths: list[str], limit: int = DIFF_LIMIT) -> str:
    """The changes to describe, for Codex to write a commit message from: tracked changes as a diff, new
    files by name."""
    root = top(root)
    has_head = _run(root, "git", "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
    tracked = git(root, "diff", "HEAD" if has_head else "--cached", "--", *paths) if paths else ""
    new = [p for p in paths if not git(root, "ls-files", "--", p).strip()]
    text = tracked
    if new:
        text += "\nNew files:\n" + "\n".join(f"  {p}" for p in new)
    if len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + f"\n... (cut short; {len(text):,} characters in all)"
    return text.strip()


def commit(root: Path, message: str, paths: list[str]) -> str:
    """Stage exactly `paths` and commit them. Returns the new commit's short id."""
    if not paths:
        raise GitError("nothing to commit")
    root = top(root)
    git(root, "add", "-A", "--", *paths)
    subject, _, body = message.strip().partition("\n")
    args = ["commit", "-q", "-m", subject.strip()]
    if body.strip():
        args += ["-m", body.strip()]
    git(root, *args)
    return git(root, "rev-parse", "--short", "HEAD").strip()


def current_branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def has_remote(root: Path, name: str = "origin") -> bool:
    return _run(root, "git", "remote", "get-url", name, check=False).returncode == 0


def default_branch(root: Path) -> str:
    """The branch pull requests go into: origin's default branch, else main or master."""
    done = _run(root, "git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if done.returncode == 0 and done.stdout.strip():
        return done.stdout.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if _run(root, "git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}", check=False).returncode == 0:
            return name
    return "main"


def commits_ahead(root: Path, base: str) -> list[tuple[str, str]]:
    """[(short id, subject)] of commits on HEAD that origin/base doesn't have, oldest first."""
    ref = f"origin/{base}"
    if _run(root, "git", "rev-parse", "--verify", "-q", ref, check=False).returncode:
        ref = base
    out = git(root, "log", "--reverse", "--format=%h%x09%s", f"{ref}..HEAD", check=False)
    return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]


def last_commit_message(root: Path) -> tuple[str, str]:
    subject = git(root, "log", "-1", "--format=%s").strip()
    body = git(root, "log", "-1", "--format=%b").strip()
    return subject, body


def branch_name(subject: str, root: Path) -> str:
    """A free branch name from a commit subject, for example codex/fix-the-login-bug."""
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40].strip("-") or "changes"
    name, n = f"codex/{slug}", 2
    while _run(root, "git", "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False).returncode == 0:
        name, n = f"codex/{slug}-{n}", n + 1
    return name


def move_to_branch(root: Path, new: str, base: str) -> None:
    """Commits made on `base` move to a new branch `new`, and `base` goes back to match GitHub. Nothing is
    lost: the commits are on `new`. Only used after the user agreed."""
    git(root, "switch", "-q", "-c", new)
    if _run(root, "git", "rev-parse", "--verify", "-q", f"origin/{base}", check=False).returncode == 0:
        git(root, "branch", "-f", base, f"origin/{base}")


def push(root: Path, branch: str) -> None:
    git(root, "push", "-q", "-u", "origin", branch)


def gh_available() -> bool:
    return shutil.which("gh") is not None


def create_pr(root: Path, base: str, branch: str, title: str, body: str) -> str:
    """Open a pull request with GitHub's gh. Returns its URL."""
    done = _run(root, "gh", "pr", "create", "--base", base, "--head", branch, "--title", title,
                "--body-file", "-", input_text=body or " ")
    urls = re.findall(r"https://\S+", done.stdout)
    return urls[-1] if urls else done.stdout.strip()
