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


def skills(codex, cwd: str, reload: bool = False) -> tuple[list, list]:
    """(skills, errors) Codex knows for this project folder: its own, the user's, and the project's."""
    response = _request(codex, "skills/list", {"cwds": [cwd], "forceReload": reload}, "SkillsListResponse")
    found, errors = [], []
    for entry in response.data or []:
        found += list(entry.skills or [])
        errors += list(entry.errors or [])
    return found, errors
