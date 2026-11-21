"""Tests for the updater, using throwaway git repositories (a fake 'GitHub', a developer copy and an
installed copy). Nothing touches the network or the real app folder."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex_app import updater  # noqa: E402


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def write_app(folder: Path, version: str, requirements: str = "anthropic\n"):
    (folder / "codex_app").mkdir(parents=True, exist_ok=True)
    (folder / "codex_app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (folder / "requirements.txt").write_text(requirements, encoding="utf-8")


def commit(folder: Path, message: str):
    git(folder, "add", "-A")
    git(folder, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", message)


@pytest.fixture
def repos(tmp_path):
    """(developer copy, installed copy), both cloned from a bare 'github' repo at version 1.0.0."""
    github = tmp_path / "github.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(github))
    dev = tmp_path / "dev"
    git(tmp_path, "clone", "-q", str(github), str(dev))
    git(dev, "checkout", "-q", "-b", "main")
    write_app(dev, "1.0.0")
    commit(dev, "First version")
    git(dev, "push", "-q", "-u", "origin", "main")
    installed = tmp_path / "installed"
    git(tmp_path, "clone", "-q", str(github), str(installed))
    return dev, installed


def publish(dev: Path, version: str, message: str, requirements: str = "anthropic\n"):
    write_app(dev, version, requirements)
    commit(dev, message)
    git(dev, "push", "-q")


def test_up_to_date(repos):
    _, installed = repos
    status = updater.check(installed)
    assert status.ok and not status.available and "1.0.0 is up to date" in status.message


def test_finds_a_new_version_with_notes(repos):
    dev, installed = repos
    publish(dev, "1.1.0", "Add a new tool")
    status = updater.check(installed)
    assert status.available and status.latest == "1.1.0" and status.behind == 1
    assert status.notes == ["Add a new tool"]
    assert updater.local_version(installed) == "1.0.0"  # checking changes nothing


def test_apply_updates_the_files(repos):
    dev, installed = repos
    publish(dev, "1.1.0", "Add a new tool")
    updated, message = updater.apply(installed, install_packages=False)
    assert updated and "1.0.0 -> 1.1.0" in message and "Add a new tool" in message
    assert updater.local_version(installed) == "1.1.0"
    assert updater.apply(installed)[0] is False  # nothing more to do


def test_refuses_to_overwrite_local_changes(repos):
    dev, installed = repos
    publish(dev, "1.1.0", "Change")
    (installed / "requirements.txt").write_text("my own edit\n", encoding="utf-8")
    updated, message = updater.apply(installed, install_packages=False)
    assert not updated and "requirements.txt" in message
    assert (installed / "requirements.txt").read_text() == "my own edit\n"


def test_refuses_when_local_commits_are_not_pushed(repos):
    dev, installed = repos
    publish(dev, "1.1.0", "Change")
    (installed / "notes.txt").write_text("x", encoding="utf-8")
    commit(installed, "local only")
    updated, message = updater.apply(installed, install_packages=False)
    assert not updated and "aren't on GitHub" in message


def test_not_a_git_clone(tmp_path):
    write_app(tmp_path, "1.0.0")
    status = updater.check(tmp_path)
    assert not status.ok and "isn't a git clone" in status.message


def test_daily_notice(repos, tmp_path):
    dev, installed = repos
    home = tmp_path / "home"
    publish(dev, "1.2.0", "Faster search")
    notice = updater.due_notice(installed, home, now=1_000_000)
    assert notice and "1.0.0 -> 1.2.0" in notice and "/update" in notice
    assert updater.due_notice(installed, home, now=1_000_000 + 3600) is None  # already checked today
    assert updater.due_notice(installed, home, now=1_000_000 + 25 * 3600) is not None
