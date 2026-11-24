"""Task profiles and power presets.

Task profiles make each kind of message as strong and as cheap as it can be:
- coding: the normal agent work (reads, edits, runs tests), at the role's own effort.
- chat:   a question or conversation. Answered directly at low effort, without the dual/team machinery,
          so it is fast and cheap.
- maths:  a calculation or proof. At least high effort, worked step by step, and checked (for example
          with a short Python calculation) before the answer is given.
"auto" picks one for each message from its wording; no extra model call is made to decide.

Power presets switch every role to stronger models and higher effort at once:
- normal: the settings you started with.
- high:   the most capable model for every role, at high effort (the planner at xhigh).
- max:    the most capable model at the highest effort Codex offers."""

import re

from .config import EFFORT_LEVELS

PROFILES = ("auto", "coding", "chat", "maths")

STRONG_MODEL = "gpt-6.2-astra"  # Codex: "our most capable model for complex coding"

# (single, planner, worker) as (model, effort); None keeps the starting setting.
POWER = {
    "normal": None,
    "high": ((STRONG_MODEL, "high"), (STRONG_MODEL, "xhigh"), (STRONG_MODEL, "high")),
    "max": ((STRONG_MODEL, "max"), (STRONG_MODEL, "max"), (STRONG_MODEL, "xhigh")),
}

GUIDANCE = {
    "coding": "",
    "chat": ("[Conversation, not a coding task] Answer directly and concisely. Use the project's files only if "
             "the question is about them. Don't change files or run commands unless the user asks.\n\n"),
    "maths": ("[Maths] Work this out step by step. Check the result before answering: run a short Python "
              "calculation where you can, or substitute the answer back. Show the key steps, and end with the "
              "final result on its own line, starting with 'Answer:'.\n\n"),
}

_CODING = re.compile(
    r"\b(fix|bug|error|exception|traceback|refactor|implement|add|create|build|write|change|update|rename|"
    r"delete|remove|install|deploy|test|tests|debug|compile|lint|commit|merge|branch|function|class|method|"
    r"module|file|files|folder|repo|code|script|api|endpoint|database|query|css|html|page|component|feature)\b"
    r"|[\w./\\-]+\.(py|js|ts|tsx|jsx|html|css|json|md|txt|yml|yaml|toml|sql|sh|cmd|ps1|java|go|rs|c|cpp|h|cs|php|rb)\b"
    r"|@\S|`[^`]+`|```",
    re.IGNORECASE)
_MATHS = re.compile(
    r"\b(solve|integral|integrate|derivative|differentiate|derive|limit|prove|proof|theorem|lemma|equation|"
    r"inequality|matrix|matrices|determinant|eigen\w*|vector|probability|expected value|variance|"
    r"permutations?|combinations?|factorial|prime|primes|gcd|lcm|modulo|logarithm|log|sin|cos|tan|"
    r"polynomial|quadratic|roots?|series|sequence|sum of|product of|calculate|compute|simplify|evaluate|"
    r"how many ways|percent(age)?|ratio|area|volume|perimeter)\b"
    r"|[∫∑∏√π∞≤≥≠±×÷^]|\d\s*[-+*/^=]\s*\d|\b\d+\s*!|\bx\s*[=<>]",
    re.IGNORECASE)
_ACTION_START = re.compile(r"^\s*(please\s+)?(fix|add|create|make|build|write|implement|change|update|"
                           r"refactor|remove|delete|run|install|set up|setup|move|rename|convert)\b", re.IGNORECASE)


def detect(text: str) -> str:
    """'coding', 'chat' or 'maths' for a message, from its wording."""
    text = text or ""
    coding = bool(_CODING.search(text)) or bool(_ACTION_START.search(text))
    maths = bool(_MATHS.search(text))
    if maths and not coding:
        return "maths"
    if coding:
        return "coding"
    return "chat"


def resolve(profile: str, text: str) -> str:
    return detect(text) if profile == "auto" else profile


def effort_for(profile: str, base: str) -> str:
    """The effort for one message: chat at most 'low', maths at least 'high', coding as set."""
    order = list(EFFORT_LEVELS)
    rank = order.index(base) if base in order else order.index("medium")
    if profile == "chat":
        return order[min(rank, order.index("low"))]
    if profile == "maths":
        return order[max(rank, order.index("high"))]
    return base


def wrap(profile: str, text: str) -> str:
    return GUIDANCE.get(profile, "") + text


def describe(profile: str) -> str:
    return {"coding": "coding", "chat": "chat: answered directly at low effort",
            "maths": "maths: high effort, worked step by step and checked"}[profile]


def apply_power(settings, level: str, start) -> None:
    """Set the models and efforts of `settings` for a power level. `start` holds the starting settings,
    which "normal" goes back to."""
    preset = POWER[level]
    if preset is None:
        preset = ((start.single_model, start.single_effort), (start.planner_model, start.planner_effort),
                  (start.worker_model, start.worker_effort))
    (settings.single_model, settings.single_effort), (settings.planner_model, settings.planner_effort), \
        (settings.worker_model, settings.worker_effort) = preset
    settings.power = level
