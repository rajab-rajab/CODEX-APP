"""Settings for Codex App. Every value can be changed with an environment variable."""

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "yes" if default else "no").lower() in ("1", "yes", "true", "on")


# Dollars per million tokens: (input, cached input, output). Set them in the app with /prices (saved in
# ~/.codex-app/prices.json and loaded into this dict at start; see costs.py). A model without a price
# shows token counts only, without a cost.
PRICES: dict = {}

EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

# What Codex may do. Codex enforces this itself with its sandbox (github.com/openai/codex).
PERMISSIONS = {
    "plan": "read only: Codex reads files and runs read-only commands, changes nothing",
    "workspace": "Codex changes files and runs commands inside the project folder only",
    "full": "no sandbox: Codex can change anything your user account can (careful)",
}

# Official Codex's names for the same three levels (its sandbox_mode values) work too.
PERMISSION_ALIASES = {"read-only": "plan", "workspace-write": "workspace", "danger-full-access": "full"}


def permission_name(name: str) -> str:
    """'plan', 'workspace' or 'full' for either this app's name or official Codex's name."""
    name = (name or "").strip().lower()
    return PERMISSION_ALIASES.get(name, name)

# What happens when Codex wants to step outside the sandbox (for example network access).
APPROVALS = {
    "deny_all": "always refused",
    "auto_review": "Codex's own reviewer decides each request",
    "ask": "you answer y/n for each request, like official Codex's on-request mode (experimental)",
}

# Where saved conversations go (one folder for all projects).
APP_HOME = Path(os.environ.get("CODEX_APP_HOME", Path.home() / ".codex-app"))


@dataclass
class Settings:
    # A) Single model mode: one model plans and does the work.
    # Models come from Codex's own list (/models). gpt-6.2-sol is Codex's default coding model.
    single_model: str = "gpt-6.2-sol"
    single_effort: str = "medium"

    # B) Dual model mode: a planner plans and reviews, a worker does the work.
    planner_model: str = "gpt-6.2-astra"   # Codex: "most capable model for complex coding"
    planner_effort: str = "high"
    worker_model: str = "gpt-6.2-sol"      # Codex: "fast, go-to model for coding"
    worker_effort: str = "medium"

    permission: str = "workspace"  # see PERMISSIONS
    approval: str = "deny_all"     # see APPROVALS
    max_review_rounds: int = 2     # dual mode: how many times the planner may send work back
    update_check: bool = True      # look for a new app version once a day at start
    web_search: bool = False       # let Codex search the web live (official Codex: --search)

    profile: str = "auto"          # auto | coding | chat | maths (see profiles.py)
    power: str = "normal"          # normal | high | max (see profiles.py)
    team_size: int = 3             # team mode: how many workers may work at the same time
    structured_verdicts: bool = True  # dual/team: the planner's verdict as structured output, not text
    secret_guard: bool = True      # stop a turn the moment Codex starts to read or change a secret file
    notify: bool = True            # beep and show a notification when a task took notify_after seconds or more
    notify_after: int = 30
    retries: int = 3               # automatic retries when OpenAI is busy (0 turns them off)
    auto_compact: bool = True      # compact the conversation by itself when little context is left
    auto_compact_at: int = 10      # ... at this percent of context left or less

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.single_model = _env("CODEX_APP_MODEL", s.single_model)
        s.single_effort = _env("CODEX_APP_EFFORT", s.single_effort)
        s.planner_model = _env("CODEX_APP_PLANNER_MODEL", s.planner_model)
        s.planner_effort = _env("CODEX_APP_PLANNER_EFFORT", s.planner_effort)
        s.worker_model = _env("CODEX_APP_WORKER_MODEL", s.worker_model)
        s.worker_effort = _env("CODEX_APP_WORKER_EFFORT", s.worker_effort)
        s.permission = permission_name(_env("CODEX_APP_PERMISSION", s.permission))
        s.approval = _env("CODEX_APP_APPROVAL", s.approval)
        s.max_review_rounds = _env_int("CODEX_APP_MAX_REVIEW_ROUNDS", s.max_review_rounds)
        s.update_check = _env_bool("CODEX_APP_UPDATE_CHECK", s.update_check)
        s.web_search = _env_bool("CODEX_APP_SEARCH", s.web_search)
        s.profile = _env("CODEX_APP_PROFILE", s.profile)
        s.power = _env("CODEX_APP_POWER", s.power)
        s.team_size = _env_int("CODEX_APP_TEAM_SIZE", s.team_size)
        s.secret_guard = _env_bool("CODEX_APP_SECRET_GUARD", s.secret_guard)
        s.notify = _env_bool("CODEX_APP_NOTIFY", s.notify)
        s.notify_after = _env_int("CODEX_APP_NOTIFY_AFTER", s.notify_after)
        s.retries = _env_int("CODEX_APP_RETRIES", s.retries)
        s.auto_compact = _env_bool("CODEX_APP_AUTO_COMPACT", s.auto_compact)
        s.auto_compact_at = _env_int("CODEX_APP_AUTO_COMPACT_AT", s.auto_compact_at)
        s.check()
        return s

    def thread_config(self) -> dict:
        """Extra Codex settings for every thread."""
        return {"web_search": "live"} if self.web_search else {}

    def check(self) -> None:
        self.permission = permission_name(self.permission)
        for effort in (self.single_effort, self.planner_effort, self.worker_effort):
            if effort not in EFFORT_LEVELS:
                raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, not '{effort}'")
        if self.permission not in PERMISSIONS:
            raise ValueError(f"permission must be one of {', '.join(PERMISSIONS)}, not '{self.permission}'")
        if self.approval not in APPROVALS:
            raise ValueError(f"approval must be one of {', '.join(APPROVALS)}, not '{self.approval}'")
        if self.profile not in ("auto", "coding", "chat", "maths"):
            raise ValueError(f"profile must be auto, coding, chat or maths, not '{self.profile}'")
        if self.power not in ("normal", "high", "max"):
            raise ValueError(f"power must be normal, high or max, not '{self.power}'")
        self.team_size = max(1, min(8, self.team_size))
