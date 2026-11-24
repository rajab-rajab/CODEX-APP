"""Shared test helpers.

Git is slow to start on Windows (about half a second per command on some PCs), and many tests need
the same throwaway repositories. `git_template` builds each kind once per test run, and every test
gets its own copy, which is only a file copy. Remotes are relative paths ("../github.git"), so a
copied clone pushes to its own copied "GitHub", never to the template or anywhere real."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def run_git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _ignore_vanished(src, names):
    # git may write and remove a temporary file while we copy; skip whatever is already gone
    return [n for n in names if not os.path.exists(os.path.join(src, n))]


@pytest.fixture(scope="session")
def git_template(tmp_path_factory):
    """git_template(name, build) -> a fresh copy of the folder `build(folder)` made, built once per run."""
    built = {}

    def copy(name: str, build, into: Path) -> Path:
        if name not in built:
            folder = tmp_path_factory.mktemp(f"template-{name}")
            build(folder)
            built[name] = folder
        target = into / name
        shutil.copytree(built[name], target, ignore=_ignore_vanished)
        return target
    return copy
