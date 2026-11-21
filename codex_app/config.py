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


# Dollars per million tokens: (input, cached input, output). Fill in the models you use from
# https://openai.com/api/pricing. A model missing here shows token counts only, without a cost.
PRICES: dict = {}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")

# What Codex may do. Codex enforces this itself with its sandbox (github.com/openai/codex).
PERMISSIONS = {
    "plan": "read only: Codex reads files and runs read-only commands, changes nothing",
    "workspace": "Codex changes files and runs commands inside the project folder only",
    "full": "no sandbox: Codex can change anything your user account can (careful)",
}

# What happens when Codex wants to step outside the sandbox (for example network access).
APPROVALS = {
    "deny_all": "always refused",
    "auto_review": "Codex's own reviewer decides each request",
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

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.single_model = _env("CODEX_APP_MODEL", s.single_model)
        s.single_effort = _env("CODEX_APP_EFFORT", s.single_effort)
        s.planner_model = _env("CODEX_APP_PLANNER_MODEL", s.planner_model)
        s.planner_effort = _env("CODEX_APP_PLANNER_EFFORT", s.planner_effort)
        s.worker_model = _env("CODEX_APP_WORKER_MODEL", s.worker_model)
        s.worker_effort = _env("CODEX_APP_WORKER_EFFORT", s.worker_effort)
        s.permission = _env("CODEX_APP_PERMISSION", s.permission)
        s.approval = _env("CODEX_APP_APPROVAL", s.approval)
        s.max_review_rounds = _env_int("CODEX_APP_MAX_REVIEW_ROUNDS", s.max_review_rounds)
        s.update_check = _env_bool("CODEX_APP_UPDATE_CHECK", s.update_check)
        s.check()
        return s

    def check(self) -> None:
        for effort in (self.single_effort, self.planner_effort, self.worker_effort):
            if effort not in EFFORT_LEVELS:
                raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, not '{effort}'")
        if self.permission not in PERMISSIONS:
            raise ValueError(f"permission must be one of {', '.join(PERMISSIONS)}, not '{self.permission}'")
        if self.approval not in APPROVALS:
            raise ValueError(f"approval must be one of {', '.join(APPROVALS)}, not '{self.approval}'")
