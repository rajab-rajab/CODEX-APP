"""Codex engine requests the SDK has no method for: undoing turns, MCP servers and skills.

They go through the SDK's lower-level client (codex._client.request), so like the "ask" approval mode
and goals they are pinned to the engine range in requirements.txt. Everything that reaches past the
SDK's public interface for these features is in this one file."""

from typing import Optional


def _request(codex, method: str, params: dict, model_name: str):
    from openai_codex.generated import v2_all
    return codex._client.request(method, params, response_model=getattr(v2_all, model_name))


def revert(codex, thread_id: str, before_turn_id: str) -> None:
    """Remove a turn, and every later one, from a Codex conversation (Codex's thread/revert). Files are
    not touched; see undo.py for those."""
    _request(codex, "thread/revert", {"threadId": thread_id, "beforeTurnId": before_turn_id}, "ThreadRevertResponse")


def mcp_servers(codex, thread_id: Optional[str] = None, limit: int = 100) -> list:
    """The MCP servers Codex is set up with, with their status and tools."""
    servers, cursor = [], None
    while True:
        params = {"detail": "toolsAndAuthOnly", "limit": limit}
        if thread_id:
            params["threadId"] = thread_id
        if cursor:
            params["cursor"] = cursor
        page = _request(codex, "mcpServerStatus/list", params, "ListMcpServerStatusResponse")
        servers += list(page.data or [])
        cursor = page.next_cursor
        if not cursor or len(servers) >= 1000:
            return servers


def start_review(codex, thread_id: str, target: dict):
    """Start Codex's own code review (review/start, what official Codex's /review uses) on a thread and
    return a TurnHandle to follow it. `target` is {"type": "uncommittedChanges"}, {"type": "baseBranch",
    "branch": ...}, {"type": "commit", "sha": ...} or {"type": "custom", "instructions": ...}.
    The turn is subscribed to while it starts, the way the SDK starts normal turns, so no event is missed."""
    from openai_codex import TurnHandle
    from openai_codex.generated.v2_all import ReviewStartResponse

    client = codex._client
    with client._thread_start_lock(thread_id):
        with client._router.pending_turn(thread_id) as cursors:
            started = client.request("review/start", {"threadId": thread_id, "target": target, "delivery": "inline"},
                                     response_model=ReviewStartResponse)
            subscription = client._router.prepare_turn(started.turn.id, thread_id, cursors, for_handle=True)
    return TurnHandle(client, thread_id, started.turn.id, _subscription=subscription)


def threads(codex, cwd: str, limit: int = 20) -> list:
    """Codex's own saved conversations for a project folder, newest first, from every Codex client:
    this app, the official Codex CLI, VS Code and others."""
    return list(codex.thread_list(cwd=cwd, limit=limit).data or [])


def rate_limits(codex):
    """The account's plan and rate-limit use (account/rateLimits/read): a RateLimitSnapshot, or None."""
    return getattr(_request(codex, "account/rateLimits/read", {}, "GetAccountRateLimitsResponse"), "rate_limits", None)


def describe_limits(snapshot, warn_at: int = 80) -> tuple[list[str], list[str]]:
    """(lines for /status, warnings) for a rate-limit snapshot. A warning is a window at warn_at% or more."""
    import time as _time

    def when(epoch):
        if not epoch:
            return ""
        left = max(0, int(epoch - _time.time()))
        hours, minutes = divmod(left // 60, 60)
        return f", resets in {hours} h {minutes} min" if hours else f", resets in {minutes} min"

    def window_name(w):
        mins = getattr(w, "window_duration_mins", None)
        if not mins:
            return "limit"
        if mins % 1440 == 0:
            return f"{mins // 1440}-day limit"
        return f"{mins // 60}-hour limit" if mins % 60 == 0 else f"{mins}-minute limit"

    lines, warnings = [], []
    if snapshot is None:
        return lines, warnings
    plan = getattr(getattr(snapshot, "plan_type", None), "value", getattr(snapshot, "plan_type", None))
    for w in (getattr(snapshot, "primary", None), getattr(snapshot, "secondary", None)):
        if w is None:
            continue
        text = f"{w.used_percent}% of your {window_name(w)} used{when(getattr(w, 'resets_at', None))}"
        lines.append(text)
        if w.used_percent >= warn_at:
            warnings.append(text)
    credits = getattr(snapshot, "credits", None)
    if credits is not None and getattr(credits, "has_credits", False) and not getattr(credits, "unlimited", False):
        lines.append(f"credits: {credits.balance}")
    if plan:
        lines.insert(0, f"plan: {plan}")
    if getattr(snapshot, "rate_limit_reached_type", None):
        warnings.insert(0, "a usage limit has been reached")
    return lines, warnings


def skills(codex, cwd: str, reload: bool = False) -> tuple[list, list]:
    """(skills, errors) Codex knows for this project folder: its own, the user's, and the project's."""
    response = _request(codex, "skills/list", {"cwds": [cwd], "forceReload": reload}, "SkillsListResponse")
    found, errors = [], []
    for entry in response.data or []:
        found += list(entry.skills or [])
        errors += list(entry.errors or [])
    return found, errors
