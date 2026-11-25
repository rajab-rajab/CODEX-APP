"""One Codex agent: a Codex thread (a conversation) driven through OpenAI's Codex Python SDK.

Codex itself (github.com/openai/codex) does the work: it reads files, runs commands and applies edits
inside its sandbox. This class starts or resumes the thread, sends messages, and turns Codex's
stream of events into short lines for the screen."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import approvals, engine, goals
from .config import PRICES

# on_event(agent_name, kind, text). kinds: "text" (streamed answer), "thinking", "tool", "tool_result",
# "tool_error", "plan" (Codex's checklist), "diff" (the turn's file changes), "notice".
EventHandler = Callable[[str, str, str], None]

# Why a turn failed (Codex's codexErrorInfo) when trying again later can help: OpenAI was busy or hit an
# internal error. Others (usage limits, bad requests, policy) won't get better by retrying.
RETRYABLE_FAILURES = {"serverOverloaded", "internalServerError"}
RETRY_DELAYS = (5, 15, 45)  # seconds before each automatic retry
CARRY_ON = ("(The last attempt stopped because OpenAI's servers were busy. Carry on with the task from where "
            "you stopped; don't redo work that is already done.)")

_PLAN_MARKS = {"completed": "[x]", "inProgress": "[>]", "in_progress": "[>]", "pending": "[ ]"}


@dataclass
class Usage:
    """Token totals for one thread, as Codex reports them."""
    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    by_model: dict = field(default_factory=dict)  # model -> [input, cached, output]
    context_used: int = 0                          # tokens in the model's context after the last turn
    context_window: int = 0                        # the model's context size (0 if Codex didn't say)

    @property
    def calls(self) -> int:  # kept for the shared /usage display
        return len(self.by_model)

    def set_total(self, model: str, total) -> None:
        self.input = getattr(total, "input_tokens", 0) or 0
        self.cached = getattr(total, "cached_input_tokens", 0) or 0
        self.output = getattr(total, "output_tokens", 0) or 0
        self.reasoning = getattr(total, "reasoning_output_tokens", 0) or 0
        self.by_model[model] = [self.input, self.cached, self.output]

    def set_context(self, last, window) -> None:
        if last is not None:
            self.context_used = (getattr(last, "total_tokens", 0)
                                 or (getattr(last, "input_tokens", 0) or 0) + (getattr(last, "output_tokens", 0) or 0))
        if window:
            self.context_window = window

    def context_left(self) -> Optional[int]:
        """Percent of the context window still free, like official Codex's "NN% context left"."""
        if not self.context_window:
            return None
        return max(0, min(100, round(100 * (1 - self.context_used / self.context_window))))

    def cost(self) -> Optional[float]:
        if not self.by_model:
            return None  # nothing used yet, so no price to show
        total = 0.0
        for model, (inp, cached, out) in self.by_model.items():
            if model not in PRICES:
                return None
            p_in, p_cached, p_out = PRICES[model]
            total += ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000
        return total

    def to_dict(self) -> dict:
        return {"input": self.input, "cached": self.cached, "output": self.output, "reasoning": self.reasoning,
                "by_model": self.by_model, "context_used": self.context_used, "context_window": self.context_window}

    @classmethod
    def from_dict(cls, data: dict) -> "Usage":
        return cls(**{k: data.get(k, 0) for k in ("input", "cached", "output", "reasoning",
                                                  "context_used", "context_window")},
                   by_model=data.get("by_model", {}))


def _retryable(error) -> bool:
    try:
        from openai_codex.errors import is_retryable_error
    except ImportError:
        return False
    return is_retryable_error(error)


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


def format_plan(payload) -> str:
    lines = []
    explanation = (getattr(payload, "explanation", None) or "").strip()
    if explanation:
        lines.append(explanation)
    for step in getattr(payload, "plan", None) or []:
        mark = _PLAN_MARKS.get(str(_value(getattr(step, "status", "pending"))), "[ ]")
        lines.append(f"{mark} {getattr(step, 'step', '')}")
    return "\n".join(lines)


class CodexAgent:
    def __init__(self, codex, *, name: str, model: str, effort: str, sandbox, approval_mode,
                 instructions: str, cwd: Path, on_event: Optional[EventHandler] = None,
                 config: Optional[dict] = None, thread_cls=None, quiet: bool = False,
                 secret_guard: Optional[Callable] = None, ephemeral: bool = False):
        self.codex = codex
        self.name = name
        self.model = model
        self.effort = effort
        self.sandbox = sandbox              # an openai_codex.Sandbox value
        self.approval_mode = approval_mode  # an openai_codex.ApprovalMode value, or approvals.ASK
        self.instructions = instructions
        self.cwd = Path(cwd)
        self.on_event = on_event or (lambda agent, kind, text: None)
        self.config = dict(config or {})    # extra Codex settings for the thread (for example web_search)
        self.thread_cls = thread_cls        # openai_codex.Thread; needed for the "ask" approval mode
        self.thread = None
        self.thread_id: Optional[str] = None
        self.usage = Usage()
        self.last_diff = ""
        self.quiet = quiet                  # show answers whole instead of streaming them (team workers)
        self._turn = None
        self._goal = None
        self._sandbox_changed = False
        self._model_changed = False
        self._cwd_changed = False
        # secret_guard(item) -> why, or None: checks each command and edit as Codex starts it (protect.py).
        self.secret_guard = secret_guard
        self.ephemeral = ephemeral          # helper conversations (reviews, commit messages) aren't saved
        self.guard_hits: list[str] = []
        # (turn id, diff) of every turn this agent ran, oldest first: what /undo goes back through.
        self.turns_log: list[tuple] = []
        self.retries = len(RETRY_DELAYS)    # automatic retries when OpenAI is busy (0 turns them off)
        self.sleep = time.sleep             # replaced in tests
        self.last_status = None             # how the last turn ended: completed, failed, interrupted
        self.last_failure = None            # why it failed (a codexErrorInfo value), or None

    # --- thread lifecycle ----------------------------------------------------------------------

    def _emit(self, kind: str, text: str) -> None:
        self.on_event(self.name, kind, text)

    def _common(self) -> dict:
        return dict(cwd=str(self.cwd), model=self.model, sandbox=self.sandbox,
                    developer_instructions=self.instructions,
                    config={"model_reasoning_effort": self.effort, **self.config})

    def _ensure_thread(self):
        if self.thread is not None:
            return self.thread
        if self.approval_mode == approvals.ASK:
            self.thread = approvals.open_thread(self.codex, self.thread_cls, self.thread_id, **self._common())
        elif self.thread_id:
            self.thread = self.codex.thread_resume(self.thread_id, approval_mode=self.approval_mode, **self._common())
        elif self.ephemeral:
            self.thread = self.codex.thread_start(approval_mode=self.approval_mode, ephemeral=True, **self._common())
        else:
            self.thread = self.codex.thread_start(approval_mode=self.approval_mode, **self._common())
        self.thread_id = self.thread.id
        return self.thread

    def reset(self) -> None:
        """Start a new thread on the next message (usage totals are kept)."""
        self.thread = None
        self.thread_id = None
        self.turns_log = []

    def discard(self) -> None:
        """Archive this agent's conversation, so it stays out of Codex's history (/history, the official
        Codex CLI's resume list), and start afresh next time. For helper conversations: reviews, commit
        messages."""
        if self.thread_id:
            try:
                self.codex.thread_archive(self.thread_id)
            except Exception:
                pass
        self.reset()

    def undo_turns(self, keep: int) -> None:
        """Take every turn after the first `keep` out of the conversation (for /undo)."""
        later = self.turns_log[keep:]
        first = next((tid for tid, _ in later if tid), None)
        if first and self.thread_id:
            engine.revert(self.codex, self.thread_id, first)
        del self.turns_log[keep:]

    def fork(self) -> Optional[str]:
        """Carry on in a copy of this conversation, leaving the original as it is. Returns the new thread ID."""
        if not self.thread_id:
            return None
        if self.approval_mode == approvals.ASK:
            self.thread = approvals.open_thread(self.codex, self.thread_cls, self.thread_id, fork=True,
                                                **self._common())
        else:
            self.thread = self.codex.thread_fork(self.thread_id, approval_mode=self.approval_mode, **self._common())
        self.thread_id = self.thread.id
        return self.thread_id

    def compact(self) -> bool:
        """Ask Codex to summarise the conversation so far, to free up context. False if there is none yet."""
        if not self.thread_id:
            return False
        self._ensure_thread().compact()
        return True

    def rename(self, name: str) -> None:
        if self.thread_id:
            self._ensure_thread().set_name(name)

    def set_sandbox(self, sandbox) -> None:
        self.sandbox = sandbox
        self._sandbox_changed = True  # sent with the next turn; Codex keeps it for later turns

    def set_cwd(self, cwd: Path) -> None:
        """Work in another folder from the next turn on (team workers in their own worktrees). The
        sandbox goes with it, so the writable folder is the new one."""
        if Path(cwd) != self.cwd:
            self.cwd = Path(cwd)
            self._cwd_changed = True

    def set_model(self, model: str, effort: Optional[str] = None) -> None:
        self.model = model
        self.effort = effort or self.effort
        self._model_changed = True    # sent with the next turn; Codex keeps it for later turns

    @property
    def busy(self) -> bool:
        """True while a turn or a goal is running."""
        return self._turn is not None or self._goal is not None

    def interrupt(self) -> None:
        for running in (self._goal, self._turn):
            if running is not None:
                try:
                    running.interrupt()
                except Exception:
                    pass

    def steer(self, message) -> bool:
        """Add instructions to the work Codex is doing right now (official Codex's steering).
        False if nothing is running."""
        running = self._goal or self._turn
        if running is None:
            return False
        running.steer(message)
        return True

    def state(self) -> dict:
        return {"thread_id": self.thread_id, "model": self.model, "usage": self.usage.to_dict()}

    def load_state(self, state: dict) -> None:
        self.reset()
        self.turns_log = []
        self.thread_id = state.get("thread_id")
        self.usage = Usage.from_dict(state.get("usage", {}))

    # --- one message ---------------------------------------------------------------------------

    def _options(self, effort: Optional[str]) -> dict:
        options = {"effort": effort or self.effort}
        if self._cwd_changed:
            options["cwd"] = str(self.cwd)
            self._sandbox_changed = True  # re-sent with it, so the sandbox follows the new folder
            self._cwd_changed = False
        if self._sandbox_changed:
            options["sandbox"] = self.sandbox
            self._sandbox_changed = False
        if self._model_changed:
            options["model"] = self.model
            self._model_changed = False
        return options

    def send(self, message, effort: Optional[str] = None, output_schema: Optional[dict] = None,
             quiet: Optional[bool] = None) -> str:
        """Give Codex a message (text, or a list of SDK input items) and stream its work until the turn
        ends. Returns its final answer.

        effort: for this message only (the role's own effort otherwise).
        output_schema: a JSON schema the final answer must follow (Codex's structured output). The answer
            is then JSON, so it isn't streamed to the screen; the caller shows it.
        quiet: don't stream the answer while it is written; show it whole at the end (for agents working
            side by side, whose streams would mix)."""
        thread = self._ensure_thread()
        options = self._options(effort)
        if output_schema is not None:
            options["output_schema"] = output_schema
        for attempt in range(self.retries + 1):
            try:
                self._turn = turn = thread.turn(message, **options)
            except Exception as e:  # the turn couldn't even start
                if attempt < self.retries and _retryable(e):
                    self._wait(attempt, f"OpenAI is busy ({e})")
                    continue
                raise
            try:
                answer = self._consume(turn.stream(), show_text=output_schema is None,
                                       stream_text=not (self.quiet if quiet is None else quiet),
                                       turn_id=getattr(turn, "id", None))
            except KeyboardInterrupt:
                self.interrupt()  # tell Codex to stop now, while the turn is still known
                raise
            finally:
                self._turn = None
            if self.last_status == "failed" and self.last_failure in RETRYABLE_FAILURES and attempt < self.retries:
                self._wait(attempt, "OpenAI's servers were busy")
                message = CARRY_ON  # not the same message again: that would repeat work already done
                options = {k: v for k, v in options.items() if k in ("effort", "output_schema")}
                continue
            return answer
        return answer

    def _wait(self, attempt: int, why: str) -> None:
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
        self._emit("notice", f"{why}; trying again in {delay} s (retry {attempt + 1} of {self.retries}). "
                             "Ctrl+C stops.")
        self.sleep(delay)

    def run_review(self, target: dict) -> str:
        """Codex's own code review (what official Codex's /review uses) on this agent's thread.
        Returns the review."""
        self._ensure_thread()
        self._turn = turn = engine.start_review(self.codex, self.thread_id, target)
        try:
            return self._consume(turn.stream(), show_text=True, stream_text=not self.quiet,
                                 turn_id=getattr(turn, "id", None))
        except KeyboardInterrupt:
            self.interrupt()
            raise
        finally:
            self._turn = None

    def run_goal(self, objective: str, token_budget: Optional[int] = None) -> str:
        """Official Codex's /goal: Codex keeps working, over as many turns as it needs, until the goal is
        done, blocked, or out of budget. Returns its final answer."""
        self._ensure_thread()
        if self._sandbox_changed or self._model_changed or self._cwd_changed:
            self._sandbox_changed = self._model_changed = self._cwd_changed = False
            self.thread = None  # resume with the new settings, so the goal's turns use them
            self._ensure_thread()
        self._goal = goal = goals.start(self.codex, self.thread_id, objective, token_budget)
        try:
            return self._consume(iter(goal), show_text=True, stream_text=not self.quiet,
                                 turn_id=getattr(goal, "first_turn_id", None))
        except KeyboardInterrupt:
            self.interrupt()  # pause the goal and stop its turn
            raise
        finally:
            self._goal = None

    def _guard(self, item, turn_id) -> bool:
        """Stop the turn if Codex starts reading or changing a secret file, and take the turn out of the
        conversation, so what it read is never sent to OpenAI with later messages. True if it stopped."""
        why = self.secret_guard(item) if self.secret_guard else None
        if not why:
            return False
        self.guard_hits.append(why)
        self._emit("tool_error", f"Stopped: {why}.")
        self.interrupt()
        return True

    def _forget_turn(self, turn_id) -> None:
        if not (turn_id and self.thread_id):
            return
        try:
            engine.revert(self.codex, self.thread_id, turn_id)
            self._emit("notice", "That turn was taken out of the conversation, so what Codex read isn't sent "
                                 "to OpenAI again. Files it changed are as they were left (/undo puts them back).")
        except Exception:
            # Some turns (a review, for example) can't be wound back: drop the conversation instead, so what
            # Codex read is never sent again. The next message starts a fresh one.
            self.reset()
            self._emit("notice", f"{self.name.capitalize()} starts a fresh conversation from the next message, "
                                 "so what Codex read isn't sent to OpenAI again.")

    def _consume(self, stream, show_text: bool, stream_text: bool, turn_id=None) -> str:
        answers, status, error, streamed = [], None, None, False
        self.last_status = self.last_failure = None
        self.last_diff = ""
        guarded = False
        for event in stream:
            method = getattr(event, "method", "")
            payload = getattr(event, "payload", None)
            if method == "item/agentMessage/delta":
                delta = getattr(payload, "delta", "")
                if delta and show_text and stream_text:
                    streamed = True
                    self._emit("text", delta)
            elif method == "item/started":
                started = _root(getattr(payload, "item", None))
                line = _describe_started(started)
                if line:
                    self._emit("tool", line)
                if not guarded and self._guard(started, turn_id):
                    guarded = True
            elif method == "item/completed":
                self._completed(_root(getattr(payload, "item", None)), answers, streamed or not show_text)
            elif method == "turn/plan/updated":
                plan = format_plan(payload)
                if plan:
                    self._emit("plan", plan)
            elif method == "turn/diff/updated":
                self.last_diff = getattr(payload, "diff", "") or ""
            elif getattr(payload, "token_usage", None) is not None:  # ThreadTokenUsageUpdatedNotification
                usage = payload.token_usage
                self.usage.set_total(self.model, usage.total)
                self.usage.set_context(getattr(usage, "last", None), getattr(usage, "model_context_window", None))
            elif method == "turn/completed":
                t = getattr(payload, "turn", None)
                status = _value(getattr(t, "status", None))
                error = getattr(t, "error", None)

        if self.last_diff.strip():
            self._emit("diff", self.last_diff)
        if turn_id is not None or self.last_diff:
            self.turns_log.append((turn_id, self.last_diff))
        if guarded:
            self._forget_turn(turn_id)
            if self.turns_log and self.turns_log[-1][0] == turn_id:
                self.turns_log.pop()  # it's out of the conversation, so /undo shouldn't look for it
            return "[Stopped: Codex tried to read or change a secret file.]"
        self.last_status = status
        if status == "failed":
            info = getattr(error, "codex_error_info", None)
            self.last_failure = str(_value(getattr(info, "root", info))) if info is not None else None
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
            repeat = bool(text.strip()) and text.strip() in (a.strip() for a in answers)  # a review shown already
            answers.append(text)
            if not streamed and text and not repeat:
                self._emit("text", text)
        elif kind == "exitedReviewMode":  # Codex's own review: the findings
            text = getattr(item, "review", "") or ""
            if text.strip() and text.strip() not in (a.strip() for a in answers):
                answers.append(text)
                if not streamed:  # it often arrives as the answer too; show it once
                    self._emit("text", text)
        elif kind == "reasoning":
            summary = " ".join(getattr(item, "summary", None) or [])
            if summary.strip():
                self._emit("thinking", summary)
        elif kind == "commandExecution":
            code = getattr(item, "exit_code", None)
            out = (getattr(item, "aggregated_output", "") or "").strip().splitlines()
            if code is None:  # refused, or stopped before it started
                line = "did not run" + (f": {_short(out[-1], 120)}" if out else "")
                self._emit("tool_error", line)
                return
            line = f"exit code {code}" + (f": {_short(out[-1], 120)}" if out else "")
            self._emit("tool_error" if code not in (0, None) else "tool_result", line)
        elif kind == "fileChange":
            status = _value(getattr(item, "status", ""))
            self._emit("tool_result" if status in ("completed", "applied", "") else "tool_error",
                       f"edit {status or 'done'}")
