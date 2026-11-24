"""Tests for /undo, the secret guard, and /mcp and /skills. Fake Codex and throwaway git repositories:
no key, nothing billed, the real app folder never touched."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import approvals, engine, protect, sessions, undo  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from codex_app.modes import SingleMode  # noqa: E402
from test_app import FakeCodex, answer, ev  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _build_site(root: Path):
    git(root, "init", "-q")
    (root / "a.txt").write_text("a1\n", encoding="utf-8")
    (root / "c.txt").write_text("c1\n", encoding="utf-8")
    (root / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "start")


@pytest.fixture
def repo(tmp_path, git_template):
    """A git project with a.txt and c.txt committed, and .env ignored."""
    return git_template("site3", _build_site, tmp_path)


class FakeClient:
    """Stands in for codex._client.request."""

    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def request(self, method, params, response_model=None):
        if method != "account/rateLimits/read":  # checked after every task; not what these tests are about
            self.calls.append((method, params))
        reply = self.replies.get(method)
        return reply(params) if callable(reply) else reply


# --- undo: file snapshots ---------------------------------------------------------------------------

def test_snapshot_and_restore(repo):
    (repo / "a.txt").write_text("a-mine\n", encoding="utf-8")  # the user's own uncommitted change
    index_before = git(repo, "ls-files", "-s")
    tree = undo.snapshot(repo)
    assert tree and git(repo, "status", "--porcelain") == " M a.txt\n"  # snapshots leave no trace

    (repo / "a.txt").write_text("a-codex\n", encoding="utf-8")  # then "Codex" works
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    (repo / "c.txt").unlink()
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    assert sorted(undo.changes(repo, tree)) == [("bring back", "c.txt"), ("delete", "b.txt"), ("restore", "a.txt")]

    undo.restore(repo, tree)
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a-mine\n"  # back to before the message, not to HEAD
    assert not (repo / "b.txt").exists() and (repo / "c.txt").read_text(encoding="utf-8") == "c1\n"
    assert (repo / ".env").read_text(encoding="utf-8") == "SECRET=1\n"  # ignored files are never touched
    assert git(repo, "ls-files", "-s") == index_before  # the user's index is untouched
    assert undo.changes(repo, tree) == []


def test_undo_stays_inside_the_project_folder(repo):
    sub = repo / "app"
    sub.mkdir()
    (sub / "x.txt").write_text("x1\n", encoding="utf-8")
    tree = undo.snapshot(sub)
    (sub / "x.txt").write_text("x2\n", encoding="utf-8")
    (repo / "a.txt").write_text("changed elsewhere\n", encoding="utf-8")
    assert undo.changes(sub, tree) == [("restore", "x.txt")]
    undo.restore(sub, tree)
    assert (repo / "a.txt").read_text(encoding="utf-8") == "changed elsewhere\n"  # outside: left alone


def test_no_git_means_no_snapshot(tmp_path):
    assert undo.snapshot(tmp_path) is None


def test_reverse_diffs_without_git(tmp_path):
    (tmp_path / "calc.py").write_text("a + b\n", encoding="utf-8")
    diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-a - b\n+a + b\n"
    assert undo.reverse_diffs(tmp_path, [diff]) == ["calc.py"]
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == "a - b\n"
    with pytest.raises(RuntimeError, match="changed since"):
        undo.reverse_diffs(tmp_path, [diff])  # already undone: refused, nothing changed


# --- undo: the command ------------------------------------------------------------------------------

def make_app(root, script, **settings):
    import main
    codex = FakeCodex(script)
    codex._client = FakeClient()
    app = main.App(codex, sdk, root, Settings(**settings), "single")
    return app, codex


def test_undo_command_puts_files_and_conversation_back(repo, capsys):
    app, codex = make_app(repo, {1: [answer("one"), answer("two")]})
    app.run_task("first")
    app.run_task("change a.txt")
    (repo / "a.txt").write_text("a-codex\n", encoding="utf-8")  # what the second message did
    app.screen.ask = None
    app.ask = lambda q: "y"
    app.command("/undo")
    out = capsys.readouterr().out
    assert "restore    a.txt" in out and "Undone" in out
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a1\n"
    assert codex._client.calls == [("thread/revert", {"threadId": "thr_1", "beforeTurnId": "thr_1_turn2"})]
    assert len(app.mode.agent.turns_log) == 1  # the first message is still there
    app.command("/undo")  # and again: back before the first message
    assert codex._client.calls[-1][1]["beforeTurnId"] == "thr_1_turn1"
    app.command("/undo")
    assert "Nothing to undo" in capsys.readouterr().out


def test_undo_needs_a_yes(repo, capsys):
    app, codex = make_app(repo, {1: [answer("one")]})
    app.run_task("change a.txt")
    (repo / "a.txt").write_text("a-codex\n", encoding="utf-8")
    app.ask = lambda q: "n"
    app.command("/undo")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "a-codex\n" and "Nothing was undone" in capsys.readouterr().out
    assert codex._client.calls == []


def test_new_conversation_clears_undo(repo, capsys):
    app, _ = make_app(repo, {1: [answer("one")]})
    app.run_task("x")
    app.command("/new")
    app.command("/undo")
    assert "Nothing to undo" in capsys.readouterr().out


# --- secrets ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,secret", [
    (".env", True), (".env.local", True), ("config/.env.production", True), (".env.example", False),
    ("id_rsa", True), ("certs/server.pem", True), ("api.key", True), ("app.db", True), ("users.sqlite3", True),
    ("data/users.json", True), ("home/.ssh/config", True), ("credentials.json", True),
    ("src/environment.py", False), ("README.md", False), ("database.py", False), ("keyboard.py", False),
    ("data", False),
])
def test_is_secret_path(path, secret):
    assert protect.is_secret_path(path) is secret


def test_secret_in_command():
    assert protect.secret_in_command("Get-Content -LiteralPath '.env'") == ".env"
    assert protect.secret_in_command("cat data/users.json | head") == "data/users.json"
    assert protect.secret_in_command('node -e "console.log(process.env.HOME)"') is None
    assert protect.secret_in_command("py -m pytest -q") is None
    assert protect.secret_in_command("ls data") is None  # listing a folder is fine


def started(**fields):
    return NS(**fields)


def test_guard_item():
    read_env = started(type="commandExecution", command="pwsh -Command 'Get-Content .env'",
                       command_actions=[NS(root=NS(type="read", path=".env", command="Get-Content .env", name=".env"))])
    assert "secret file (.env)" in protect.guard_item(read_env)
    edit_key = started(type="fileChange", changes=[NS(path="keys/server.key")])
    assert "change a secret file" in protect.guard_item(edit_key)
    fine = started(type="commandExecution", command="py -m pytest", command_actions=[NS(root=NS(type="unknown", command="py -m pytest"))])
    assert protect.guard_item(fine) is None


def test_find_secret_files(tmp_path):
    for rel in (".env", ".env.example", "data/users.json", "node_modules/pkg/.env", "src/app.py", "db/app.sqlite"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert protect.find_secret_files(tmp_path) == [".env", "data/users.json", "db/app.sqlite"]


def test_the_guard_stops_the_turn_and_takes_it_out_of_the_conversation(tmp_path):
    events = []
    read_env = ev("item/started", item=NS(root=NS(type="commandExecution", command="Get-Content .env",
                                                  command_actions=[NS(root=NS(type="read", path=".env",
                                                                               command="Get-Content .env", name=".env"))])))
    codex = FakeCodex({1: [[read_env] + answer("SECRET=hunter2")]})
    codex._client = FakeClient()
    mode = SingleMode(codex, sdk, tmp_path, Settings(), on_event=lambda a, k, t: events.append((k, t)))
    reply = mode.run("what's in the config?")
    assert reply.startswith("[Stopped") and "hunter2" not in reply
    assert ("tool_error", "Stopped: Codex started to read a secret file (.env).") in events
    assert codex._client.calls == [("thread/revert", {"threadId": "thr_1", "beforeTurnId": "thr_1_turn1"})]
    assert mode.agent.turns_log == []


def test_the_guard_can_be_turned_off(tmp_path):
    read_env = ev("item/started", item=NS(root=NS(type="fileChange", changes=[NS(path=".env")])))
    codex = FakeCodex({1: [[read_env] + answer("edited")]})
    assert SingleMode(codex, sdk, tmp_path, Settings(secret_guard=False)).run("x") == "edited"


def test_approval_requests_for_secret_files_are_refused():
    said = []
    a = approvals.Approver(ask=False, notice=said.append)
    request = {"command": "pwsh -Command 'type .env'", "commandActions": [{"type": "read", "command": "type .env"}]}
    assert a(approvals.COMMAND_APPROVAL, request) == {"decision": "decline"}
    assert "secret file (.env)" in said[-1]


def test_secrets_command(tmp_path, capsys):
    (tmp_path / ".env").write_text("x", encoding="utf-8")
    app, _ = make_app(tmp_path, {})
    app.warn_secrets()
    app.command("/secrets")
    app.command("/secrets off")
    out = capsys.readouterr().out
    assert "Secret files in this project: .env" in out and "  .env" in out and app.settings.secret_guard is False


# --- MCP servers and skills ---------------------------------------------------------------------------

def test_mcp_servers_pages_through_the_list():
    pages = iter([NS(data=[NS(name="github")], next_cursor="2"), NS(data=[NS(name="db")], next_cursor=None)])
    client = FakeClient({"mcpServerStatus/list": lambda params: next(pages)})
    servers = engine.mcp_servers(NS(_client=client), "thr_1")
    assert [s.name for s in servers] == ["github", "db"]
    assert client.calls[1][1] == {"detail": "toolsAndAuthOnly", "limit": 100, "threadId": "thr_1", "cursor": "2"}


def test_mcp_command(tmp_path, capsys):
    app, codex = make_app(tmp_path, {})
    codex._client.replies["mcpServerStatus/list"] = NS(data=[NS(
        name="github", runtime_status=NS(value="connected"), auth_status=NS(value="oAuth"),
        tools={"create_issue": {}, "search": {}}, tools_error=None)], next_cursor=None)
    app.command("/mcp")
    out = capsys.readouterr().out
    assert "github: connected, sign-in: oAuth, 2 tools" in out and "create_issue, search" in out
    codex._client.replies["mcpServerStatus/list"] = NS(data=[], next_cursor=None)
    app.command("/mcp")
    assert "config.toml" in capsys.readouterr().out


def skill(name, path, enabled=True):
    return NS(name=name, path=path, enabled=enabled, description=f"{name} skill", short_description=None,
              scope=NS(value="user"))


def test_skills_command_and_dollar_mentions(tmp_path, capsys):
    app, codex = make_app(tmp_path, {1: [answer("done")]})
    codex._client.replies["skills/list"] = NS(data=[NS(cwd=str(tmp_path), errors=[], skills=[
        skill("pdf", "/skills/pdf/SKILL.md"), skill("old", "/skills/old/SKILL.md", enabled=False)])])
    app.command("/skills")
    out = capsys.readouterr().out
    assert "$pdf" in out and "(turned off)" in out
    app.run_task("make a report with $pdf, not $old, costing $5")
    sent = codex.turns[0]["text"]
    assert sdk.SkillInput("pdf", "/skills/pdf/SKILL.md") in sent
    assert not any(isinstance(x, sdk.SkillInput) and x.name == "old" for x in sent)


# --- no child process may inherit the app's input ----------------------------------------------------

def test_git_never_inherits_the_apps_input(repo, monkeypatch):
    """On Windows a child that inherits the input the steering thread reads can hang at start (found in a
    real run: /diff and /undo hung). Every git the app starts gets its own input."""
    import main
    from codex_app import updater
    seen = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        if cmd and cmd[0] == "git":
            seen.append(kw.get("stdin") is subprocess.DEVNULL or "input" in kw)
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(subprocess, "run", spy)
    undo.snapshot(repo)
    main.git_changes(repo)
    updater.check(repo, fetch=False)
    assert seen and all(seen)
