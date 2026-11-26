"""Tests for --json, /export, /prices and /stats. Fake Codex only: no key, nothing billed."""

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import openai_codex as sdk  # noqa: E402

from codex_app import config, costs, export, sessions  # noqa: E402
from codex_app.config import Settings  # noqa: E402
from test_app import FakeCodex, answer, ev  # noqa: E402


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "APP_HOME", tmp_path / "home")
    monkeypatch.setattr(config, "APP_HOME", tmp_path / "home")
    saved = dict(config.PRICES)  # the same dict the agents read: change it in place, put it back after
    config.PRICES.clear()
    yield
    config.PRICES.clear()
    config.PRICES.update(saved)


def used(inp, cached, out):
    return answer("x")[:2] + [
        ev("thread/tokenUsage/updated", token_usage=NS(total=NS(input_tokens=inp, cached_input_tokens=cached,
                                                                output_tokens=out, reasoning_output_tokens=0))),
        ev("turn/completed", turn=NS(status=NS(value="completed"), error=None))]


def make_app(root, script=None, mode="single", **settings):
    import main
    codex = FakeCodex(script or {})
    return main.App(codex, sdk, root, Settings(**settings), mode), codex


# --- /prices ------------------------------------------------------------------------------------------

def test_prices_are_saved_and_used(tmp_path, capsys):
    app, _ = make_app(tmp_path)
    app.command("/prices gpt-6.2-sol 1.25 0.125 10")
    assert config.PRICES["gpt-6.2-sol"] == (1.25, 0.125, 10.0)
    config.PRICES.clear()
    assert costs.load_prices() == {"gpt-6.2-sol": (1.25, 0.125, 10.0)}  # read back from prices.json
    app.command("/prices")
    assert "gpt-6.2-sol" in capsys.readouterr().out
    app.command("/prices gpt-6.2-sol off")
    assert "gpt-6.2-sol" not in config.PRICES
    app.command("/prices gpt x y z")
    assert "numbers" in capsys.readouterr().out


def test_cost_shows_after_a_task_once_a_price_is_set(tmp_path, capsys):
    costs.set_price("gpt-6.1-terra", 2, 0.5, 10)
    app, _ = make_app(tmp_path, {1: [used(1_000_000, 0, 100_000)]})
    app.run_task("fix it")
    assert "about $3.0000" in capsys.readouterr().out


# --- /stats -------------------------------------------------------------------------------------------

def test_each_task_is_logged_and_summed(tmp_path, capsys):
    app, _ = make_app(tmp_path, {1: [used(1000, 200, 100), used(3000, 1000, 300)]})
    app.run_task("one")
    app.run_task("two")  # Codex reports running totals: only the difference is logged
    log = costs.read_log()
    assert [e["models"] for e in log] == [{"gpt-6.1-terra": [1000, 200, 100]}, {"gpt-6.1-terra": [2000, 800, 200]}]
    assert log[0]["project"] == str(tmp_path) and "fix" not in json.dumps(log)  # no messages in the log
    app.command("/stats")
    out = capsys.readouterr().out
    assert "3,300 tokens" in out and "No price for gpt-6.1-terra" in out
    costs.set_price("gpt-6.1-terra", 1, 0.5, 10)
    app.command("/stats")
    # (800 x $1 + 200 x $0.5 + 100 x $10) + (1200 x $1 + 800 x $0.5 + 200 x $10), per million: today's prices,
    # for earlier work too
    assert "Total: 3,300 tokens, $0.0055" in capsys.readouterr().out


def test_stats_by_day_and_project():
    agent = NS(name="codex", usage=NS(by_model={"m": [100, 0, 10]}))
    now = time.time()
    costs.record({}, [agent], "/p/a", "single", now=now - 3 * 86400)
    costs.record({}, [agent], "/p/b", "dual", now=now)
    week = costs.summary(7, now=now)
    assert week["total"][0] == 220 and len(week["days"]) == 2 and {p for p, _, _ in week["projects"]} == {"/p/a", "/p/b"}
    assert costs.summary(1, now=now)["total"][0] == 110


def test_stats_with_nothing_logged(tmp_path, capsys):
    app, _ = make_app(tmp_path)
    app.command("/stats all")
    assert "No usage recorded yet" in capsys.readouterr().out


# --- --json ----------------------------------------------------------------------------------------------

def test_result_reports_answer_files_and_tokens(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    costs.set_price("gpt-6.1-terra", 1, 0.5, 10)
    app, codex = make_app(root, {1: [used(1_000_000, 0, 100_000)]})
    original_send = app.mode.agent.send

    def send_and_change(message, **kw):
        (root / "b.txt").write_text("new", encoding="utf-8")  # what "Codex" did
        return original_send(message, **kw)
    app.mode.agent.send = send_and_change
    app.run_task("add b.txt")
    result = app.result()
    assert result["ok"] is True and result["answer"] == "x" and result["mode"] == "single"
    assert result["files_changed"] == [{"path": "b.txt", "change": "added"}]
    assert result["tokens"] == {"gpt-6.1-terra": {"input": 1_000_000, "cached": 0, "output": 100_000}}
    assert result["cost_usd"] == 2.0
    json.dumps(result)  # must be plain JSON


def test_result_reports_failure(tmp_path):
    failed = [ev("turn/completed", turn=NS(status=NS(value="failed"),
                                          error=NS(message="no", codex_error_info=NS(root=NS(value="usageLimitExceeded")))))]
    app, _ = make_app(tmp_path, {1: [failed]})
    app.run_task("x")
    result = app.result()
    assert result["ok"] is False and result["error"] == "usageLimitExceeded"


def test_json_needs_a_task(capsys):
    import main
    assert main.main(["--json"]) == 2
    assert "--json needs a task" in capsys.readouterr().err


# --- /export ---------------------------------------------------------------------------------------------

def item(**fields):
    return NS(root=NS(**fields))


def fake_thread(turns):
    return NS(read=lambda include_turns=False: NS(thread=NS(turns=turns)))


def test_render_markdown():
    turns = [NS(items=[
        item(type="userMessage", content=[NS(root=NS(type="text", text="[Maths] Work this out.\n\nwhat is 2+2?"))]),
        item(type="reasoning", summary=["Adding"]),
        item(type="commandExecution", command="py -c \"print(2+2)\"", exit_code=0, aggregated_output="4\n"),
        item(type="fileChange", changes=[NS(path="calc.py", kind=NS(root=NS(type="update")), diff="-a\n+b\n")]),
        item(type="agentMessage", text="Answer: 4"),
    ])]
    text = export.render([("codex", fake_thread(turns))], "Sums", "/p", "single")
    assert text.startswith("# Sums\n")
    assert "### You\n\nwhat is 2+2?" in text and "[Maths]" not in text
    assert "**Command** (exit code 0)" in text and "$ py -c" in text and "\n4\n" in text
    assert "**Update `calc.py`**" in text and "```diff\n-a\n+b\n```" in text
    assert "### Codex\n\nAnswer: 4" in text and "*Thinking: Adding*" in text


def test_export_command_writes_the_file(tmp_path, capsys, monkeypatch):
    app, codex = make_app(tmp_path, {1: [answer("hello there")]})
    app.command("/export")
    assert "Nothing to export yet" in capsys.readouterr().out
    app.run_task("say hello")
    turns = [NS(items=[item(type="userMessage", content=[NS(root=NS(type="text", text="say hello"))]),
                       item(type="agentMessage", text="hello there")])]
    monkeypatch.setattr(app.mode.agent, "_ensure_thread", lambda: fake_thread(turns))
    target = tmp_path / "out" / "chat"
    app.command(f"/export {target}")
    written = (tmp_path / "out" / "chat.md").read_text(encoding="utf-8")
    assert "### You\n\nsay hello" in written and "### Codex\n\nhello there" in written
    app.command("/export")
    assert list((config.APP_HOME / "exports").glob("*.md"))  # the default place


# --- the GitHub helpers --------------------------------------------------------------------------------

def test_engine_watch_widens_to_the_next_minor_version():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "engine_watch", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "engine_watch.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    from packaging.version import Version
    assert watch.widened("openai-codex>=0.171,<0.200", Version("0.205.3")) == "openai-codex>=0.171,<0.206"
    assert watch.widened("openai-codex<0.200", Version("1.2.0")) == "openai-codex<1.3"
    assert watch.requirement_line("# engine\nopenai-codex>=0.171,<0.200  # range\npytest\n") == "openai-codex>=0.171,<0.200"


def test_workflows_are_valid_yaml():
    yaml = pytest.importorskip("yaml")
    folder = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    names = {f.name: yaml.safe_load(f.read_text(encoding="utf-8")) for f in folder.glob("*.yml")}
    assert {"tests.yml", "engine-watch.yml", "release.yml"} <= set(names)
    assert set(names["engine-watch.yml"]["jobs"]) == {"newest", "test", "widen", "report"}
