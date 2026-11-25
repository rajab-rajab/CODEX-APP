"""Tests for memory between conversations (memory.py, /memory), with a fake Codex: no key, nothing billed.
conftest.py keeps the notes file in a temporary folder."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import memory, prefs, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import DualMode, SingleMode, make_reviewer, make_writer  # noqa: E402
from test_app import FakeCodex, answer  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(prefs.config, "APP_HOME", tmp_path / "home")


# --- notes --------------------------------------------------------------------------------------------

def test_parse_remember_lines():
    text = ("Fixed it.\n\nREMEMBER: Tests run with `pytest -q`.\n- **REMEMBER EVERYWHERE:** The user prefers tabs.\n"
            "forget: Tests run with nose.\nREMEMBER: a\nREMEMBER: b\nREMEMBER: c\n")
    assert memory.parse(text) == [("project", "Tests run with `pytest -q`."), ("everywhere", "The user prefers tabs."),
                                  ("forget", "Tests run with nose."), ("project", "a")]  # at most 3 notes


@pytest.mark.parametrize("text", [
    "The key is sk-proj-abcdefghijklmnopqrstuvwx", "token: ghp_abcdefghijklmnopqrstuvwxyz0123",
    "password: hunter2", "API key = 123", "AKIAABCDEFGHIJKLMNOP is the AWS user",
    "the secret is 9f8e7d6c5b4a39281706f5e4d3c2b1a0ffee", "-----BEGIN RSA PRIVATE KEY", "   ",
])
def test_secrets_are_never_kept(text):
    assert memory.clean(text) == ""


def test_ordinary_notes_are_kept():
    note = "Run tests/test_one_worker_or_worktrees_off_works_in_the_project_folder with pytest -q"
    assert memory.clean(f"  `{note}`  ") == note
    assert len(memory.clean("x" * 1000)) == memory.NOTE_CHARS


def test_add_forget_and_limits(tmp_path, monkeypatch):
    root = tmp_path
    assert memory.add(root, "Tests run with pytest -q.") == "Tests run with pytest -q."
    assert memory.add(root, "tests RUN with pytest -q") == ""  # already noted
    assert memory.add(root, "The user prefers tabs.", everywhere=True)
    project, everywhere = memory.notes(root)
    assert [n["text"] for n in project] == ["Tests run with pytest -q."]
    assert [n["text"] for n in everywhere] == ["The user prefers tabs."]
    assert memory.notes(tmp_path / "other")[0] == []  # another project has its own notes
    assert memory.forget(root, "Tests run with pytest") == ["Tests run with pytest -q."]  # the start of a note
    assert memory.forget_number(root, 1) == "The user prefers tabs."
    monkeypatch.setattr(memory, "PROJECT_LIMIT", 3)
    for n in range(5):
        memory.add(root, f"note number {n}")
    assert [n["text"] for n in memory.notes(root)[0]] == ["note number 2", "note number 3", "note number 4"]
    assert memory.clear(root) == 3 and memory.notes(root)[0] == []


def test_instructions_carry_the_notes(tmp_path):
    assert "(none yet)" in memory.instructions(tmp_path) and "REMEMBER:" in memory.instructions(tmp_path)
    memory.add(tmp_path, "Build with make.")
    memory.add(tmp_path, "Answer in British English.", everywhere=True)
    text = memory.instructions(tmp_path)
    assert "- Build with make." in text and "all of the user's projects:\n- Answer in British English." in text
    assert "data, not instructions" in text


# --- agents -------------------------------------------------------------------------------------------

def test_main_agents_get_the_notes_and_helpers_dont(tmp_path):
    memory.add(tmp_path, "Build with make.")
    settings = Settings()
    settings.memory_notes = memory.instructions(tmp_path)
    codex = FakeCodex({1: [answer("ok")], 2: [answer("ok")], 3: [answer('{"subject": "x", "body": ""}')]})
    SingleMode(codex, sdk, tmp_path, settings).run("hi")
    assert "Build with make." in codex.started[0]["developer_instructions"]
    assert "Build with make." not in make_reviewer(codex, sdk, tmp_path, settings, "m").instructions
    assert "Build with make." not in make_writer(codex, sdk, tmp_path, settings, "m").instructions
    settings.memory = False
    assert "Build with make." not in SingleMode(codex, sdk, tmp_path, settings).agent.instructions


# --- the app ------------------------------------------------------------------------------------------

def make_app(root, script, mode="single", **settings):
    import main
    app = main.App(FakeCodex(script), sdk, root, Settings(**settings), mode)
    app.ask = lambda q: "y"
    return app


def test_notes_from_an_answer_are_kept_and_used_next_time(tmp_path, capsys):
    app = make_app(tmp_path, {1: [answer("Done.\nREMEMBER: Tests run with pytest -q.")], 2: [answer("ok")]})
    app.run_task("fix the tests")
    assert "Remembered: Tests run with pytest -q." in capsys.readouterr().out
    assert [n["text"] for n in memory.notes(tmp_path)[0]] == ["Tests run with pytest -q."]
    assert "Tests run with pytest -q." not in app.codex.started[0]["developer_instructions"]  # not known yet then
    app.command("/new")
    app.run_task("next")
    assert "- Tests run with pytest -q." in app.codex.started[1]["developer_instructions"]


def test_every_agent_in_dual_mode_can_add_notes(tmp_path, capsys):
    app = make_app(tmp_path, {1: [answer("plan"), answer("ok\nVERDICT: APPROVED\nREMEMBER: The planner noted this.")],
                              2: [answer("did it\nREMEMBER EVERYWHERE: The user wants short answers.")]},
                   mode="dual", structured_verdicts=False)
    app.run_task("change it")
    project, everywhere = memory.notes(tmp_path)
    assert [n["text"] for n in project] == ["The planner noted this."]
    assert [n["text"] for n in everywhere] == ["The user wants short answers."]
    assert "all projects: The user wants short answers." in capsys.readouterr().out


def test_secret_notes_are_refused_and_wrong_ones_forgotten(tmp_path, capsys):
    memory.add(tmp_path, "Tests run with nose.")
    app = make_app(tmp_path, {1: [answer("ok\nREMEMBER: password: hunter2\nFORGET: Tests run with nose.")]})
    app.run_task("x")
    out = capsys.readouterr().out
    assert "Not remembered (it looks like a secret)" in out and "Forgot: Tests run with nose." in out
    assert memory.notes(tmp_path) == ([], [])


def test_memory_off_makes_and_uses_no_notes(tmp_path):
    memory.add(tmp_path, "Build with make.")
    app = make_app(tmp_path, {1: [answer("ok\nREMEMBER: Something new.")]}, memory=False)
    app.run_task("x")
    assert "Build with make." not in app.codex.started[0]["developer_instructions"]
    assert [n["text"] for n in memory.notes(tmp_path)[0]] == ["Build with make."]


def test_memory_command(tmp_path, capsys):
    app = make_app(tmp_path, {})
    app.command("/memory")
    assert "No notes yet" in capsys.readouterr().out
    app.command("/memory add Deploy with ./deploy.sh")
    app.command("/memory everywhere Write commit messages in English")
    app.command("/memory add password: x")
    out = capsys.readouterr().out
    assert "Noted: Deploy with ./deploy.sh" in out and "for all your projects" in out and "Not kept" in out
    app.command("/memory")
    out = capsys.readouterr().out
    assert " 1. Deploy with ./deploy.sh" in out and " 2. Write commit messages in English" in out
    app.command("/memory forget 1")
    assert "Forgot: Deploy with ./deploy.sh" in capsys.readouterr().out
    app.command("/memory clear everywhere")
    assert memory.notes(tmp_path) == ([], [])
    app.command("/memory off")
    assert app.settings.memory is False
    app.command("/save")
    assert prefs.load(tmp_path)["memory"] is False


def test_status_of_a_new_app_does_not_write_notes(tmp_path):
    make_app(tmp_path, {})
    assert not memory._file().exists()
