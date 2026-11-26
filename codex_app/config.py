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
REVIEW_CHOICES = ("auto", "always", "never")


def review_name(value: str) -> str:
    """auto, always or never; yes/on mean always and no/off never (CODEX_APP_PLANNER_REVIEW=no)."""
    value = str(value).strip().lower()
    return {"yes": "always", "on": "always", "true": "always", "1": "always",
            "no": "never", "off": "never", "false": "never", "0": "never"}.get(value, value)

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
    # dual/team: when the planner reviews the work. auto: not when the worker's tests passed and the change is
    # small (review_max_lines changed lines or fewer) and no browser check or merge needs it; always; never.
    planner_review: str = "auto"
    review_max_lines: int = 200
    review_effort: str = ""        # the review's effort; "" = one level below the planner's, at least medium
    update_check: bool = True      # look for a new app version once a day at start
    web_search: bool = False       # let Codex search the web live (official Codex: --search)

    profile: str = "auto"          # auto | coding | chat | maths (see profiles.py)
    power: str = "normal"          # normal | high | max (see profiles.py)
    team_size: int = 3             # team mode: how many workers may work at the same time
    team_worktrees: bool = True    # team mode: each worker in its own git worktree (see worktrees.py)
    memory: bool = True            # notes kept between conversations (see memory.py)
    fix_permissions: bool = True   # Windows: give back files Codex's sandbox made unreadable (see perms.py)
    memory_notes: str = ""         # set by the app: what main agents are told about memory (not a setting to save)
    structured_verdicts: bool = True  # dual/team: the planner's verdict as structured output, not text
    secret_guard: bool = True      # stop a turn the moment Codex starts to read or change a secret file
    notify: bool = True            # beep and show a notification when a task took notify_after seconds or more
    notify_after: int = 30
    retries: int = 3               # automatic retries when OpenAI is busy (0 turns them off)
    auto_compact: bool = True      # compact the conversation by itself when little context is left
    auto_compact_at: int = 10      # ... at this percent of context left or less
    outside_tools: bool = False    # computer use, browsers and account plugins (see outside.py): off by default
    browser: bool = False          # Codex App's own private browser (see browser_mcp.py), for main agents
    browser_channel: str = "msedge"  # msedge | chrome | chromium
    browser_headless: bool = False   # no window (for servers); by default you can watch it work
    browser_server: object = None    # set by the app when the browser is ready (not a setting to save)
    browser_sites: str = ""          # comma-separated allowlist: only these sites open (for shared installs)
    browser_private: bool = False    # allow private network addresses (routers, intranets): off
    browser_check: bool = True       # dual/team: web changes are looked at in the browser before the verdict
    check_url: str = ""              # ... at this address (a dev server you run); else the app serves the folder

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
        s.planner_review = review_name(_env("CODEX_APP_PLANNER_REVIEW", s.planner_review))
        s.review_max_lines = _env_int("CODEX_APP_REVIEW_MAX_LINES", s.review_max_lines)
        s.review_effort = _env("CODEX_APP_REVIEW_EFFORT", s.review_effort)
        s.update_check = _env_bool("CODEX_APP_UPDATE_CHECK", s.update_check)
        s.web_search = _env_bool("CODEX_APP_SEARCH", s.web_search)
        s.profile = _env("CODEX_APP_PROFILE", s.profile)
        s.power = _env("CODEX_APP_POWER", s.power)
        s.team_size = _env_int("CODEX_APP_TEAM_SIZE", s.team_size)
        s.team_worktrees = _env_bool("CODEX_APP_TEAM_WORKTREES", s.team_worktrees)
        s.memory = _env_bool("CODEX_APP_MEMORY", s.memory)
        s.fix_permissions = _env_bool("CODEX_APP_FIX_PERMISSIONS", s.fix_permissions)
        s.secret_guard = _env_bool("CODEX_APP_SECRET_GUARD", s.secret_guard)
        s.notify = _env_bool("CODEX_APP_NOTIFY", s.notify)
        s.notify_after = _env_int("CODEX_APP_NOTIFY_AFTER", s.notify_after)
        s.retries = _env_int("CODEX_APP_RETRIES", s.retries)
        s.auto_compact = _env_bool("CODEX_APP_AUTO_COMPACT", s.auto_compact)
        s.auto_compact_at = _env_int("CODEX_APP_AUTO_COMPACT_AT", s.auto_compact_at)
        s.outside_tools = _env_bool("CODEX_APP_OUTSIDE_TOOLS", s.outside_tools)
        s.browser = _env_bool("CODEX_APP_BROWSER", s.browser)
        s.browser_channel = _env("CODEX_APP_BROWSER_CHANNEL", s.browser_channel)
        s.browser_headless = _env_bool("CODEX_APP_BROWSER_HEADLESS", s.browser_headless)
        s.browser_sites = _env("CODEX_APP_BROWSER_SITES", s.browser_sites)
        s.browser_private = _env_bool("CODEX_APP_BROWSER_PRIVATE", s.browser_private)
        s.browser_check = _env_bool("CODEX_APP_BROWSER_CHECK", s.browser_check)
        s.check_url = _env("CODEX_APP_CHECK_URL", s.check_url)
        s.check()
        return s

    def review_effort_now(self) -> str:
        """The effort of the planner's review: review_effort, or one level below the planner's own effort
        (high -> medium), never below medium unless the planner itself is lower. A review checks a diff the
        worker already tested; it needs less reasoning than the plan."""
        if self.review_effort:
            return self.review_effort
        if self.planner_effort not in EFFORT_LEVELS:
            return self.planner_effort
        i, floor = EFFORT_LEVELS.index(self.planner_effort), EFFORT_LEVELS.index("medium")
        return EFFORT_LEVELS[i - 1] if i > floor else self.planner_effort

    def thread_config(self, browser: bool = True) -> dict:
        """Extra Codex settings for a thread. browser=False for helpers (reviews, commit messages)."""
        config = {"web_search": "live"} if self.web_search else {}
        if browser and self.browser and self.browser_server:
            config["mcp_servers"] = {"codex_browser": self.browser_server}
        return config

    def check(self) -> None:
        self.permission = permission_name(self.permission)
        for effort in (self.single_effort, self.planner_effort, self.worker_effort):
            if effort not in EFFORT_LEVELS:
                raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, not '{effort}'")
        if self.review_effort and self.review_effort not in EFFORT_LEVELS:
            raise ValueError(f"review effort must be one of {', '.join(EFFORT_LEVELS)}, not '{self.review_effort}'")
        self.planner_review = review_name(self.planner_review)
        if self.planner_review not in REVIEW_CHOICES:
            raise ValueError(f"planner review must be auto, always or never, not '{self.planner_review}'")
        self.review_max_lines = max(0, self.review_max_lines)
        if self.permission not in PERMISSIONS:
            raise ValueError(f"permission must be one of {', '.join(PERMISSIONS)}, not '{self.permission}'")
        if self.approval not in APPROVALS:
            raise ValueError(f"approval must be one of {', '.join(APPROVALS)}, not '{self.approval}'")
        if self.profile not in ("auto", "coding", "chat", "maths"):
            raise ValueError(f"profile must be auto, coding, chat or maths, not '{self.profile}'")
        if self.power not in ("normal", "high", "max"):
            raise ValueError(f"power must be normal, high or max, not '{self.power}'")
        self.team_size = max(1, min(8, self.team_size))
