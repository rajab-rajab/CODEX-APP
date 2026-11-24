"""Real costs: /prices (what your models cost) and /stats (a running log of tokens and cost).

Prices are dollars per million tokens: input, cached input, output. They live in
~/.codex-app/prices.json and fill config.PRICES at start, so /usage and the line after each task show
dollars. Every task's tokens are added to ~/.codex-app/usage.jsonl (one line per task: when, project,
mode, tokens per model; never your messages), which /stats sums up per day and per project. Costs in
/stats use today's prices, so setting a price later shows the cost of earlier work too."""

import json
import time
from pathlib import Path
from typing import Optional

from . import config


def _prices_file() -> Path:
    return config.APP_HOME / "prices.json"


def _log_file() -> Path:
    return config.APP_HOME / "usage.jsonl"


# --- prices -------------------------------------------------------------------------------------------

def load_prices() -> dict:
    """Read the saved prices into config.PRICES (kept in place, so everything sees them). Returns them."""
    try:
        saved = json.loads(_prices_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    for model, value in (saved.items() if isinstance(saved, dict) else []):
        if isinstance(value, list) and len(value) == 3:
            config.PRICES[model] = tuple(float(v) for v in value)
    return dict(config.PRICES)


def _save_prices() -> None:
    _prices_file().parent.mkdir(parents=True, exist_ok=True)
    _prices_file().write_text(json.dumps({m: list(p) for m, p in config.PRICES.items()}, indent=1), encoding="utf-8")


def set_price(model: str, inp: float, cached: float, out: float) -> None:
    if min(inp, cached, out) < 0:
        raise ValueError("prices can't be negative")
    config.PRICES[model] = (float(inp), float(cached), float(out))
    _save_prices()


def remove_price(model: str) -> bool:
    if model not in config.PRICES:
        return False
    del config.PRICES[model]
    _save_prices()
    return True


def cost_of(model: str, inp: int, cached: int, out: int) -> Optional[float]:
    price = config.PRICES.get(model)
    if price is None:
        return None
    p_in, p_cached, p_out = price
    return ((inp - cached) * p_in + cached * p_cached + out * p_out) / 1_000_000


# --- the usage log -------------------------------------------------------------------------------------

def snapshot(agents) -> dict:
    """Each agent's token totals per model now, to measure one task by."""
    return {(a.name, model): list(totals) for a in agents for model, totals in a.usage.by_model.items()}


def record(before: dict, agents, project, mode: str, now: Optional[float] = None) -> dict:
    """Add what the agents used since `before` to the log. Returns {model: [input, cached, output]}."""
    used: dict = {}
    for (name, model), totals in snapshot(agents).items():
        start = before.get((name, model), [0, 0, 0])
        delta = [max(0, t - s) for t, s in zip(totals, start)]
        if any(delta):
            used[model] = [u + d for u, d in zip(used.get(model, [0, 0, 0]), delta)]
    if used:
        now = time.time() if now is None else now
        line = {"time": int(now), "day": time.strftime("%Y-%m-%d", time.localtime(now)),
                "project": str(project), "mode": mode, "models": used}
        try:
            _log_file().parent.mkdir(parents=True, exist_ok=True)
            with _log_file().open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
        except OSError:
            pass
    return used


def read_log() -> list[dict]:
    try:
        lines = _log_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summary(days: int = 7, project: Optional[str] = None, now: Optional[float] = None) -> dict:
    """{"days": [(day, tokens, cost)], "projects": [(project, tokens, cost)], "total": (tokens, cost),
    "unpriced": {models without a price}} for the last `days` days. Cost is None where a model has no price."""
    now = time.time() if now is None else now
    since = now - days * 86400
    by_day, by_project, unpriced = {}, {}, set()
    total_tokens, total_cost = 0, 0.0
    for entry in read_log():
        if entry.get("time", 0) < since or (project and entry.get("project") != project):
            continue
        for model, (inp, cached, out) in (entry.get("models") or {}).items():
            tokens = inp + out
            cost = cost_of(model, inp, cached, out)
            if cost is None:
                unpriced.add(model)
            for bucket, key in ((by_day, entry.get("day", "?")), (by_project, entry.get("project", "?"))):
                t, c = bucket.get(key, (0, 0.0))
                bucket[key] = (t + tokens, c + (cost or 0.0))
            total_tokens += tokens
            total_cost += cost or 0.0
    return {"days": sorted(((d, t, c) for d, (t, c) in by_day.items()), reverse=True),
            "projects": sorted(((p, t, c) for p, (t, c) in by_project.items()), key=lambda x: -x[1]),
            "total": (total_tokens, total_cost), "unpriced": unpriced}
