"""Provenance envelope — wire format for every query execution response.

See `03-interaction-contract.md §3.1` for the canonical TypeScript shape.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.adapters.base import QueryPlan
from backend.query_engine.verifier.verdict import Verdict, VerifiedResolution


def _plan_json(plan: QueryPlan) -> dict:
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


class Provenance(BaseModel):
    candidateSource: str
    patternId: str | None = None
    patternMatchConfidence: float | None = None

    verifierVerdict: str
    verifierVerified: bool
    verifierLatencyMs: int | None = None
    verifierCacheHit: bool | None = None
    verifierNote: str | None = None
    reroutedPlan: dict | None = None
    originalCandidate: dict | None = None

    synthesised: bool = False
    synthesisConfidence: float | None = None

    queryPlan: dict
    generatedSql: str | None = None

    executionLatencyMs: int = 0
    adapter: str = "postgresql"
    skillHash: str
    llmCostUsd: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExecutedResult(BaseModel):
    result: dict
    provenance: Provenance
    sessionId: str = Field(default_factory=lambda: f"sess_{uuid4().hex[:16]}")


def build_provenance(
    *,
    resolution: VerifiedResolution,
    execution_latency_ms: int,
    skill_hash: str,
    adapter_key: str,
    generated_sql: str | None,
    expose_sql: bool,
) -> Provenance:
    cand = resolution.original_candidate
    plan = resolution.effective_plan

    synthesised = cand.source == "synthesised" or resolution.verdict == Verdict.REJECT_PREFER_LLM
    return Provenance(
        candidateSource=cand.source,
        patternId=cand.pattern_id,
        patternMatchConfidence=cand.pattern_match_confidence,
        verifierVerdict=resolution.verdict.value,
        verifierVerified=resolution.verified,
        verifierLatencyMs=resolution.latency_ms or None,
        verifierCacheHit=resolution.cache_hit,
        verifierNote=resolution.note,
        reroutedPlan=(
            _plan_json(resolution.rerouted_plan)
            if resolution.rerouted_plan else None
        ),
        originalCandidate=(
            _plan_json(cand.query_plan)
            if resolution.verdict == Verdict.REJECT_PREFER_LLM else None
        ),
        synthesised=synthesised,
        synthesisConfidence=cand.synthesis_confidence,
        queryPlan=_plan_json(plan),
        generatedSql=generated_sql if expose_sql else None,
        executionLatencyMs=execution_latency_ms,
        adapter=adapter_key,
        skillHash=skill_hash,
        llmCostUsd=resolution.llm_cost_usd,
    )


def compute_skill_hash(registry) -> str:
    """Stable hash of the registry's entity/field structure.

    Used by the verdict cache and saved-view staleness detection. When the
    schema changes, the key changes automatically and stale entries drop.
    """
    parts = []
    for name in sorted(registry.entity_by_name.keys()):
        skill = registry.entity_by_name[name]
        fields = ",".join(
            f"{f.name}:{f.type}:{int(f.sensitive)}:{int(f.isPK)}"
            for f in sorted(skill.fields, key=lambda x: x.name)
        )
        parts.append(f"{name}|{skill.table}|{fields}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
