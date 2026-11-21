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


NO_ENGINE_UPDATE = lambda: updater.EngineStatus(True, "Codex engine 0.171.0 is up to date.", "0.171.0", "0.171.0")  # noqa: E731


def test_daily_notice(repos, tmp_path):
    dev, installed = repos
    home = tmp_path / "home"
    publish(dev, "1.2.0", "Faster search")
    notice = updater.due_notice(installed, home, now=1_000_000, engine=NO_ENGINE_UPDATE)
    assert notice and "1.0.0 -> 1.2.0" in notice and "/update" in notice
    assert updater.due_notice(installed, home, now=1_000_000 + 3600, engine=NO_ENGINE_UPDATE) is None  # checked today
    assert updater.due_notice(installed, home, now=1_000_000 + 25 * 3600, engine=NO_ENGINE_UPDATE) is not None


# --- the Codex engine --------------------------------------------------------------------------------

def releases(*versions, yanked=()):
    """Fake PyPI 'releases' data: each version has one file; some can be yanked."""
    return lambda: {v: [{"filename": f"x-{v}.whl", "yanked": v in yanked}] for v in versions}


@pytest.fixture
def app_with_range(tmp_path):
    (tmp_path / "requirements.txt").write_text("# engine\nopenai-codex>=0.171,<0.200  # range\npytest\n",
                                               encoding="utf-8")
    return tmp_path


def test_engine_requirement_is_read_from_requirements(app_with_range):
    assert updater.engine_requirement(app_with_range) == "openai-codex>=0.171,<0.200"


def test_engine_newest_accepted_version(app_with_range):
    status = updater.engine_check(app_with_range, releases("0.170.0", "0.171.0", "0.180.2", "0.181.0rc1",
                                                           "0.185.0", "0.200.0"), current="0.171.0")
    assert status.available and status.latest == "0.185.0"  # newest inside the range; pre-releases and 0.200 are skipped
    assert status.newest == "0.200.0" and "0.171.0 -> 0.185.0" in status.message


def test_engine_yanked_versions_are_skipped(app_with_range):
    status = updater.engine_check(app_with_range, releases("0.171.0", "0.172.0", yanked=("0.172.0",)),
                                  current="0.171.0")
    assert not status.available and "up to date" in status.message


def test_engine_outside_range_is_mentioned_not_installed(app_with_range):
    status = updater.engine_check(app_with_range, releases("0.171.0", "0.200.0"), current="0.171.0")
    assert not status.available and "0.200.0 exists" in status.message


def test_engine_pypi_unreachable(app_with_range):
    def down():
        raise OSError("no network")
    status = updater.engine_check(app_with_range, down, current="0.171.0")
    assert not status.ok and "no network" in status.message


class FakePip:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[-1])
        return type("Done", (), {"returncode": 1 if self.fail else 0, "stderr": "boom"})()


def test_engine_apply_installs_and_checks(app_with_range):
    pip = FakePip()
    status = updater.EngineStatus(True, "x", "0.171.0", "0.185.0")
    ok, message = updater.engine_apply(app_with_range, status, run=pip, starts=lambda: True)
    assert ok and pip.calls == ["openai-codex==0.185.0"] and "0.171.0 -> 0.185.0" in message


def test_engine_apply_rolls_back_if_new_engine_does_not_start(app_with_range):
    pip = FakePip()
    status = updater.EngineStatus(True, "x", "0.171.0", "0.185.0")
    ok, message = updater.engine_apply(app_with_range, status, run=pip, starts=lambda: False)
    assert not ok and pip.calls == ["openai-codex==0.185.0", "openai-codex==0.171.0"]
    assert "put back" in message


def test_engine_apply_reports_pip_failure(app_with_range):
    status = updater.EngineStatus(True, "x", "0.171.0", "0.185.0")
    ok, message = updater.engine_apply(app_with_range, status, run=FakePip(fail=True), starts=lambda: True)
    assert not ok and "could not be updated" in message


def test_daily_notice_mentions_engine(tmp_path):
    engine = lambda: updater.EngineStatus(True, "Codex engine update available: 0.171.0 -> 0.185.0.",  # noqa: E731
                                          "0.171.0", "0.185.0")
    notice = updater.due_notice(tmp_path, tmp_path / "home", now=1_000_000, engine=engine)
    assert "0.171.0 -> 0.185.0" in notice and "py main.py --update" in notice
