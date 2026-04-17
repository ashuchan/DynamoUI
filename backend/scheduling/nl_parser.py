"""NL-to-schedule parser.

Recognises cadence phrases ("weekly", "every Monday at 9am", "daily") and
channel hints ("email me", "to alice@corp.com"). Falls back to LLM only on
novel phrasings — not wired in this pass; leave the hook for later.
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import HTTPException

from backend.scheduling.models.dtos import ScheduleDraft
from backend.scheduling.services.cron_parser import next_runs

_CADENCE_MAP = {
    "hourly": "0 * * * *",
    "every hour": "0 * * * *",
    "daily": "0 9 * * *",
    "every day": "0 9 * * *",
    "weekly": "0 9 * * MON",
    "every week": "0 9 * * MON",
    "every monday": "0 9 * * MON",
    "every tuesday": "0 9 * * TUE",
    "every wednesday": "0 9 * * WED",
    "every thursday": "0 9 * * THU",
    "every friday": "0 9 * * FRI",
    "monthly": "0 9 1 * *",
    "every month": "0 9 1 * *",
}

_EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")


def _extract_cron(text: str) -> str | None:
    low = text.lower()
    for phrase, cron in _CADENCE_MAP.items():
        if phrase in low:
            return cron
    return None


def _extract_channel(text: str, current_user_email: str | None) -> tuple[str, dict]:
    emails = _EMAIL_RE.findall(text)
    if emails:
        return "email", {"to": emails}
    if "email me" in text.lower() or "send me" in text.lower():
        return "email", {"to": [current_user_email] if current_user_email else []}
    if "slack" in text.lower():
        return "slack", {}
    if "webhook" in text.lower():
        return "webhook", {}
    return "email", {"to": [current_user_email] if current_user_email else []}


async def parse_schedule_nl(
    text: str, app, *, user_email: str | None = None
) -> ScheduleDraft:
    """Parse a scheduling NL input into a ScheduleDraft. Does NOT persist."""
    cron = _extract_cron(text)
    if cron is None:
        raise HTTPException(
            status_code=422,
            detail="could not detect cadence (daily/weekly/monthly/etc.)",
        )
    channel, channel_cfg = _extract_channel(text, user_email)

    # Resolve the sub-intent (the query/chart) via the pipeline, but do NOT
    # execute. For now we simply forward the whole input as the "snapshot"
    # request — the UI confirms, the user clicks save, the schedule creation
    # resolves the NL against the view at save time.
    return ScheduleDraft(
        sourceType="synthesised",
        cronExpr=cron,
        timezone="UTC",
        channel=channel,
        channelConfig=channel_cfg,
        format="html_snapshot",
        sourceSnapshot={"nl_input": text, "suggestedName": text[:60]},
        nextRuns=next_runs(cron),
    )
