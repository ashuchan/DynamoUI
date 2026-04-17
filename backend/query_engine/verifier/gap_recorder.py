"""Pattern gap recorder.

Writes rejection gap entries to dynamoui_internal.pattern_gap. If the internal
engine is unavailable the recorder degrades to a warning log — gap recording
is best-effort telemetry, never fatal to a query.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.query_engine.verifier.verdict import (
    CandidateResolution,
    PatternGapSuggestion,
)

log = structlog.get_logger(__name__)


def _input_hash(user_input: str, entity: str) -> str:
    return hashlib.sha256(f"{user_input.strip().lower()}|{entity}".encode()).hexdigest()


class PatternGapRecorder:
    def __init__(self, engine: AsyncEngine | None, schema: str) -> None:
        self._engine = engine
        self._schema = schema

    async def record(
        self,
        *,
        user_input: str,
        rejected: CandidateResolution,
        llm_plan,
        suggestion: PatternGapSuggestion | None,
        user_id: UUID | None,
    ) -> None:
        if self._engine is None:
            log.warning("pattern_gap.no_engine", entity=rejected.entity)
            return

        h = _input_hash(user_input, rejected.entity)

        try:
            async with self._engine.begin() as conn:
                table_ref = sa.text(
                    f'SELECT id, occurrence_count FROM {self._schema}.pattern_gap '
                    'WHERE input_hash = :h LIMIT 1'
                )
                existing = (
                    await conn.execute(table_ref, {"h": h})
                ).mappings().first()

                if existing is not None:
                    await conn.execute(
                        sa.text(
                            f'UPDATE {self._schema}.pattern_gap SET '
                            'occurrence_count = occurrence_count + 1, '
                            'updated_at = NOW() WHERE id = :id'
                        ),
                        {"id": existing["id"]},
                    )
                else:
                    await conn.execute(
                        sa.text(
                            f'INSERT INTO {self._schema}.pattern_gap '
                            '(id, input_hash, user_input, rejected_candidate_json, '
                            ' llm_plan_json, gap_suggestion_json, entity, user_id, '
                            ' resolved, occurrence_count, created_at, updated_at) '
                            'VALUES (:id, :h, :ui, :rc, :lp, :gs, :e, :u, false, 1, NOW(), NOW())'
                        ),
                        {
                            "id": uuid4(),
                            "h": h,
                            "ui": user_input[:2000],
                            "rc": json.dumps(_plan_to_dict(rejected.query_plan)),
                            "lp": json.dumps(_plan_to_dict(llm_plan)) if llm_plan else None,
                            "gs": json.dumps(asdict(suggestion)) if suggestion else None,
                            "e": rejected.entity,
                            "u": str(user_id) if user_id else None,
                        },
                    )
        except Exception as exc:
            log.warning("pattern_gap.record_failed", error=str(exc))


def _plan_to_dict(plan) -> dict:
    if plan is None:
        return {}
    return {
        "entity": plan.entity,
        "filters": [asdict(f) for f in plan.filters],
        "sort": [asdict(s) for s in plan.sort],
        "joins": [asdict(j) for j in plan.joins],
        "aggregations": [asdict(a) for a in plan.aggregations],
        "group_by": plan.group_by,
        "result_limit": plan.result_limit,
        "page": plan.page,
        "page_size": plan.page_size,
        "select_fields": plan.select_fields,
    }
