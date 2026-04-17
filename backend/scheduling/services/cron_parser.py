"""Cron parsing + next-fire-time preview.

Uses ``croniter`` at runtime when available; falls back to a tiny validator
that accepts the 5-field expressions we care about plus presets.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

_PRESETS = {
    "@hourly": "0 * * * *",
    "@daily": "0 9 * * *",
    "@weekly": "0 9 * * MON",
    "@monthly": "0 9 1 * *",
}

MIN_INTERVAL_MINUTES = 15


def normalise(expr: str) -> str:
    expr = (expr or "").strip()
    return _PRESETS.get(expr, expr)


def validate(expr: str) -> None:
    """Validate a cron expression. Raises ValueError on invalid input."""
    e = normalise(expr)
    parts = e.split()
    if len(parts) != 5:
        raise ValueError("cron must be 5 fields: minute hour dom month dow")

    minute, hour, *_ = parts
    # Reject "*" on minute (per-minute granularity). Enforce min-interval if possible.
    if minute == "*":
        raise ValueError(
            f"per-minute schedules are not allowed (min interval {MIN_INTERVAL_MINUTES} minutes)"
        )
    if minute.startswith("*/"):
        try:
            step = int(minute[2:])
        except ValueError:
            raise ValueError("invalid step minute expression")
        if step < MIN_INTERVAL_MINUTES:
            raise ValueError(
                f"step minute {step} is below min interval {MIN_INTERVAL_MINUTES}"
            )


def next_runs(
    expr: str, *, tz: str = "UTC", count: int = 5
) -> list[str]:
    """Return the next N fire times as ISO 8601 strings. Uses croniter if present."""
    e = normalise(expr)
    validate(e)
    try:
        from croniter import croniter  # type: ignore

        base = datetime.now(timezone.utc)
        it = croniter(e, base)
        return [it.get_next(datetime).isoformat() for _ in range(count)]
    except ImportError:
        # Minimal heuristic — advance by 1 hour per step.
        base = datetime.now(timezone.utc)
        return [(base + timedelta(hours=i + 1)).isoformat() for i in range(count)]
