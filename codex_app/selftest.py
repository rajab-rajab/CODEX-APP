"""`py main.py --selftest`: check that the installed Codex engine still has everything Codex App uses.

Several features reach past the SDK's public interface: the "ask" approval mode, goals, /undo, team
plans and verdicts (structured output), /review, /history, MCP servers, skills and limits. A new engine
could change any of them. This checks all of them in a few seconds, without a single model turn, so
nothing is billed:

1. The SDK names and engine data types the app uses still exist and look the same.
2. The engine still answers the requests the app sends: it starts an "ask" conversation and one with
   web search, lists MCP servers, skills, limits and conversations, then removes what it made.
3. Methods that can't be tried for real without billing (undoing a turn, clearing a goal) are called with
   a made-up id: "invalid request" means the method is there, "method not found" means it's gone.

`py main.py --update` runs this after installing a new engine, and puts the old one back if it fails."""

import inspect
import sys
import tempfile
from dataclasses import fields
from typing import Callable

CHECKS: list = []


def check(name: str, live: bool = False):
    def register(fn: Callable):
        CHECKS.append((name, live, fn))
        return fn
    return register


def _fields(cls) -> set:
    return set(getattr(cls, "model_fields", {}) or {})


def _need(condition, what: str) -> None:
    if not condition:
        raise AssertionError(what)


# --- 1. names and types -------------------------------------------------------------------------------

@check("SDK: approval modes, sandboxes and inputs")
def _sdk_basics(sdk, codex, workdir):
    _need([m.value for m in sdk.ApprovalMode] == ["deny_all", "auto_review"], "ApprovalMode changed")
    _need([m.value for m in sdk.Sandbox] == ["read-only", "workspace-write", "full-access"], "Sandbox changed")
    for name in ("TextInput", "LocalImageInput", "MentionInput", "SkillInput", "Thread", "TurnHandle",
                 "CodexConfig", "CodexError", "ServerBusyError", "InvalidRequestError", "MethodNotFoundError"):
        _need(hasattr(sdk, name), f"openai_codex.{name} is missing")
    turn = inspect.signature(sdk.Thread.turn).parameters
    _need({"effort", "model", "sandbox", "output_schema", "cwd"} <= set(turn),
          "Thread.turn lost parameters (cwd: team mode's worktrees)")
    _need(list(inspect.signature(sdk.Thread).parameters)[:2] == ["_client", "id"], "Thread(_client, id) changed")
    for name in ("steer", "interrupt", "stream"):
        _need(hasattr(sdk.TurnHandle, name), f"TurnHandle.{name} is missing")
    for name in ("compact", "set_name", "read"):
        _need(hasattr(sdk.Thread, name), f"Thread.{name} is missing")


@check("SDK internals: ask mode, goals, review")
def _sdk_internals(sdk, codex, workdir):
    from openai_codex import client as c
    from openai_codex import _goal

    src = inspect.getsource(c.CodexClient)
    _need("self._approval_handler" in src, "the approval handler hook is gone (ask mode)")
    for name in ("thread_start", "thread_resume", "thread_fork"):
        _need(list(inspect.signature(getattr(c.CodexClient, name)).parameters)[-1] == "params",
              f"CodexClient.{name} changed (ask mode)")
    for name in ("start_goal_operation", "next_goal_notification", "unregister_goal_operation",
                 "cancel_goal_operation", "turn_steer", "request", "_thread_start_lock"):
        _need(hasattr(c.CodexClient, name), f"CodexClient.{name} is missing")
    _need({f.name for f in fields(_goal._GoalNotificationStream)} >= {"state", "next_notification", "unregister",
                                                                       "cancel_goal"}, "the goal stream changed")
    from openai_codex import _message_router as r
    for name in ("pending_turn", "prepare_turn"):
        _need(hasattr(r.MessageRouter if hasattr(r, "MessageRouter") else type(codex._client._router), name),
              f"router.{name} is missing (/review)")


@check("Engine data: events, errors, undo, review, history")
def _schema(sdk, codex, workdir):
    from openai_codex.generated import notification_registry, v2_all as v

    registry = inspect.getsource(notification_registry)
    for method in ("item/agentMessage/delta", "item/started", "item/completed", "turn/completed",
                   "thread/tokenUsage/updated", "turn/plan/updated", "turn/diff/updated"):
        _need(f'"{method}"' in registry, f"the {method} event is gone")
    _need({"last", "model_context_window", "total"} <= _fields(v.ThreadTokenUsage), "token usage changed")
    _need({"plan", "explanation"} <= _fields(v.TurnPlanUpdatedNotification), "plan events changed")
    _need("diff" in _fields(v.TurnDiffUpdatedNotification), "diff events changed")
    _need("command_actions" in _fields(v.CommandExecutionThreadItem), "command actions are gone (secret guard)")
    _need({"serverOverloaded", "internalServerError"} <= {x.value for x in v.CodexErrorInfoValue},
          "error kinds changed (retries)")
    _need({"thread_id", "before_turn_id"} <= _fields(v.ThreadRevertParams), "thread/revert changed (/undo)")
    _need({"thread_id", "target"} <= _fields(v.ReviewStartParams), "review/start changed (/review)")
    _need("review" in _fields(v.ExitedReviewModeThreadItem), "review results changed (/review)")
    _need("live" in {x.value for x in v.WebSearchMode}, "web search modes changed")
    for name in ("GetAccountRateLimitsResponse", "ListMcpServerStatusResponse", "SkillsListResponse",
                 "ThreadGoalSetResponse", "ThreadRevertResponse", "ReviewStartResponse"):
        _need(hasattr(v, name), f"{name} is missing")


# --- 2. live requests -----------------------------------------------------------------------------------

@check("Engine: ask-mode and web-search conversations", live=True)
def _threads(sdk, codex, workdir):
    from . import approvals
    _need(approvals.install(codex, approvals.Approver()), "can't install the approval handler")
    common = dict(cwd=workdir, sandbox=sdk.Sandbox.workspace_write, developer_instructions="self-test",
                  config={"model_reasoning_effort": "low"})
    made = [approvals.open_thread(codex, sdk.Thread, **common),
            codex.thread_start(approval_mode=sdk.ApprovalMode.deny_all,
                               **{**common, "config": {"model_reasoning_effort": "low", "web_search": "live"}})]
    try:
        for thread in made:
            _need(thread.read().thread.path, "new conversations aren't saved (goals need that)")
    finally:
        for thread in made:
            try:
                codex.thread_archive(thread.id)
            except Exception:
                pass


@check("Engine: undo and goal methods are there", live=True)
def _methods(sdk, codex, workdir):
    from . import engine
    thread = codex.thread_start(approval_mode=sdk.ApprovalMode.deny_all, cwd=workdir)
    try:
        for what, call in (("thread/revert (/undo)", lambda: engine.revert(codex, thread.id, "no-such-turn")),
                           ("thread/goal/clear (goals)", lambda: codex._client.thread_goal_clear(thread.id))):
            try:
                call()
            except sdk.MethodNotFoundError:
                raise AssertionError(f"{what} is gone")
            except sdk.CodexError:
                pass  # refused for the made-up id: the method is there
    finally:
        try:
            codex.thread_archive(thread.id)
        except Exception:
            pass


@check("Engine: MCP servers, skills, history and limits", live=True)
def _lists(sdk, codex, workdir):
    from . import engine
    # A made-up server name answers at once; listing all would wait for every MCP server to start.
    engine._request(codex, "mcpServerStatus/list", {"detail": "toolsAndAuthOnly", "serverName": "__selftest__",
                                                    "limit": 1}, "ListMcpServerStatusResponse")
    engine.skills(codex, workdir)
    engine.threads(codex, workdir, limit=1)
    try:
        engine.rate_limits(codex)
    except sdk.CodexError as e:
        if "chatgpt authentication required" not in str(e).lower():  # API-key sign-ins have no plan limits
            raise


def run(live: bool = True, codex=None) -> list[tuple[str, bool, str]]:
    """[(check, passed, why it failed)]."""
    import openai_codex as sdk
    results = []
    own = None
    with tempfile.TemporaryDirectory(prefix="codex-app-selftest-") as workdir:
        try:
            if live and codex is None:
                own = codex = sdk.Codex()
            for name, needs_engine, fn in CHECKS:
                if needs_engine and not live:
                    continue
                try:
                    fn(sdk, codex, workdir)
                    results.append((name, True, ""))
                except Exception as e:
                    results.append((name, False, f"{type(e).__name__}: {e}"))
        except Exception as e:
            results.append(("Engine: start", False, f"{type(e).__name__}: {e}"))
        finally:
            if own is not None:
                own.close()
    return results


def main(quiet: bool = False, offline: bool = False) -> int:
    """offline: only the checks that don't start the engine (for CI, where nobody is signed in)."""
    try:
        results = run(live=not offline)
    except ImportError as e:
        print(f"The Codex SDK can't be loaded: {e}")
        return 1
    failed = [r for r in results if not r[1]]
    for name, ok, why in results:
        if not quiet or not ok:
            print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f": {why}" if why else ""))
    if not quiet:
        from importlib.metadata import version
        print(f"Codex engine {version('openai-codex')}: " + ("everything Codex App uses works." if not failed else
              f"{len(failed)} check{'s' if len(failed) > 1 else ''} failed; the features above may not work."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
