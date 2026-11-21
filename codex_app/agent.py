"""One Codex agent: a Codex thread (a conversation) driven through OpenAI's Codex Python SDK.

Codex itself (github.com/openai/codex) does the work: it reads files, runs commands and applies edits
inside its sandbox. This class starts or resumes the thread, sends messages, and turns Codex's
stream of events into short lines for the screen."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config import PRICES

# on_event(agent_name, kind, text). kinds: "text" (streamed answer), "thinking", "tool", "tool_result",
# "tool_error", "notice".
EventHandler = Callable[[str, str, str], None]


@dataclass
class Usage:
    """Token totals for one thread, as Codex reports them."""
    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    by_model: dict = field(default_factory=dict)  # model -> [input, cached, output]

    @property
    def calls(self) -> int:  # kept for the shared /usage display
        return len(self.by_model)

    def set_total(self, model: str, total) -> None:
        self.input = getattr(total, "input_tokens", 0) or 0
        self.cached = getattr(total, "cached_input_tokens", 0) or 0
        self.output = getattr(total, "output_tokens", 0) or 0
        self.reasoning = getattr(total, "reasoning_output_tokens", 0) or 0
        self.by_model[model] = [self.input, self.cached, self.output]

    def cost(self) -> Optional[float]:
        total = 0.0
        for model, (inp, cached, out) in self.by_model.items():
            if model not in PRICES:
                return None
            p_in, p_cached, p_out = PRICES[model]
            total += ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
        return total

    def to_dict(self) -> dict:
        return {"input": self.input, "cached": self.cached, "output": self.output,
                "reasoning": self.reasoning, "by_model": self.by_model}

    @classmethod
    def from_dict(cls, data: dict) -> "Usage":
        return cls(**{k: data.get(k, 0) for k in ("input", "cached", "output", "reasoning")},
                   by_model=data.get("by_model", {}))


def _root(item):
    """Codex items arrive wrapped (item.root); unwrap when needed."""
    return getattr(item, "root", item)


def _value(x):
    return getattr(x, "value", x)


def _short(text, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _describe_started(item) -> Optional[str]:
    kind = getattr(item, "type", "")
    if kind == "commandExecution":
        return f"run: {_short(getattr(item, 'command', ''))}"
    if kind == "fileChange":
        paths = [getattr(c, "path", "?") for c in (getattr(item, "changes", None) or [])]
        return f"edit: {', '.join(paths) or 'files'}"
    if kind == "webSearch":
        return f"web search: {_short(getattr(item, 'query', ''))}"
    if kind == "mcpToolCall":
        return f"tool: {getattr(item, 'tool', '') or getattr(item, 'name', '')}"
    return None


class CodexAgent:
    def __init__(self, codex, *, name: str, model: str, effort: str, sandbox, approval_mode,
                 instructions: str, cwd: Path, on_event: Optional[EventHandler] = None):
        self.codex = codex
        self.name = name
        self.model = model
        self.effort = effort
        self.sandbox = sandbox              # an openai_codex.Sandbox value
        self.approval_mode = approval_mode  # an openai_codex.ApprovalMode value
        self.instructions = instructions
        self.cwd = Path(cwd)
        self.on_event = on_event or (lambda agent, kind, text: None)
        self.thread = None
        self.thread_id: Optional[str] = None
        self.usage = Usage()
        self._turn = None
        self._sandbox_changed = False

    # --- thread lifecycle ----------------------------------------------------------------------

    def _emit(self, kind: str, text: str) -> None:
        self.on_event(self.name, kind, text)

    def _ensure_thread(self):
        if self.thread is not None:
            return self.thread
        common = dict(cwd=str(self.cwd), model=self.model, sandbox=self.sandbox,
                      approval_mode=self.approval_mode, developer_instructions=self.instructions,
                      config={"model_reasoning_effort": self.effort})
        if self.thread_id:
            self.thread = self.codex.thread_resume(self.thread_id, **common)
        else:
            self.thread = self.codex.thread_start(**common)
            self.thread_id = self.thread.id
        return self.thread

    def reset(self) -> None:
        """Start a new thread on the next message (usage totals are kept)."""
        self.thread = None
        self.thread_id = None

    def set_sandbox(self, sandbox) -> None:
        self.sandbox = sandbox
        self._sandbox_changed = True  # sent with the next turn; Codex keeps it for later turns

    def interrupt(self) -> None:
        if self._turn is not None:
            try:
                self._turn.interrupt()
            except Exception:
                pass

    def state(self) -> dict:
        return {"thread_id": self.thread_id, "model": self.model, "usage": self.usage.to_dict()}

    def load_state(self, state: dict) -> None:
        self.reset()
        self.thread_id = state.get("thread_id")
        self.usage = Usage.from_dict(state.get("usage", {}))

    # --- one message ---------------------------------------------------------------------------

    def send(self, message: str) -> str:
        """Give Codex a message and stream its work until the turn ends. Returns its final answer."""
        thread = self._ensure_thread()
        options = {"effort": self.effort}
        if self._sandbox_changed:
            options["sandbox"] = self.sandbox
            self._sandbox_changed = False
        self._turn = turn = thread.turn(message, **options)
        answers, status, error, streamed = [], None, None, False
        try:
            for event in turn.stream():
                method = getattr(event, "method", "")
                payload = getattr(event, "payload", None)
                if method == "item/agentMessage/delta":
                    delta = getattr(payload, "delta", "")
                    if delta:
                        streamed = True
                        self._emit("text", delta)
                elif method == "item/started":
                    line = _describe_started(_root(getattr(payload, "item", None)))
                    if line:
                        self._emit("tool", line)
                elif method == "item/completed":
                    self._completed(_root(getattr(payload, "item", None)), answers, streamed)
                elif getattr(payload, "token_usage", None) is not None:  # ThreadTokenUsageUpdatedNotification
                    self.usage.set_total(self.model, payload.token_usage.total)
                elif method == "turn/completed":
                    t = getattr(payload, "turn", None)
                    status = _value(getattr(t, "status", None))
                    error = getattr(t, "error", None)
        finally:
            self._turn = None

        if status == "failed":
            why = getattr(error, "message", None) or str(error or "unknown error")
            self._emit("notice", f"Codex could not finish: {why}")
            return f"[Codex could not finish: {why}]"
        if status == "interrupted":
            self._emit("notice", "Stopped.")
        return (answers[-1] if answers else "").strip()

    def _completed(self, item, answers: list, streamed: bool) -> None:
        kind = getattr(item, "type", "")
        if kind == "agentMessage":
            text = getattr(item, "text", "") or ""
            answers.append(text)
            if not streamed and text:
                self._emit("text", text)
        elif kind == "reasoning":
            summary = " ".join(getattr(item, "summary", None) or [])
            if summary.strip():
                self._emit("thinking", summary)
        elif kind == "commandExecution":
            code = getattr(item, "exit_code", None)
            out = (getattr(item, "aggregated_output", "") or "").strip().splitlines()
            line = f"exit code {code}" + (f": {_short(out[-1], 120)}" if out else "")
            self._emit("tool_error" if code not in (0, None) else "tool_result", line)
        elif kind == "fileChange":
            status = _value(getattr(item, "status", ""))
            self._emit("tool_result" if status in ("completed", "applied", "") else "tool_error",
                       f"edit {status or 'done'}")
