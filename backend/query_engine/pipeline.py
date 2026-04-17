"""Resolve pipeline — candidate resolution → verifier → execute → provenance.

Stage order per v2 plan §3.1:
  input → normalise → classify → candidate_resolution
                                → LLM_VERIFIER (toggleable) → execute
"""
from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import structlog

from backend.adapters.base import QueryPlan
from backend.adapters.registry import get_adapter_registry
from backend.pattern_cache.cache.pattern_cache import PatternCache
from backend.query_engine.provenance import (
    ExecutedResult,
    build_provenance,
    compute_skill_hash,
)
from backend.query_engine.verifier.llm_verifier import LLMVerifier
from backend.query_engine.verifier.verdict import (
    CandidateResolution,
    Verdict,
    VerifiedResolution,
)
from backend.skill_registry.config.settings import FeatureFlagSettings
from backend.skill_registry.llm.query_synthesiser import QuerySynthesiser
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)


class ResolvePipeline:
    """Pulls candidates (pattern cache | LLM synth) and runs them through the verifier."""

    def __init__(
        self,
        *,
        registry: SkillRegistry,
        pattern_cache: PatternCache,
        synthesiser: QuerySynthesiser | None,
        verifier: LLMVerifier,
        features: FeatureFlagSettings,
    ) -> None:
        self._registry = registry
        self._pattern_cache = pattern_cache
        self._synthesiser = synthesiser
        self._verifier = verifier
        self._features = features

    async def resolve(
        self,
        *,
        user_input: str,
        user_id: UUID | None = None,
    ) -> ExecutedResult | None:
        """Resolve an NL input end-to-end. Returns None if we couldn't get a plan.

        Callers are responsible for wrapping this in metering and mapping None
        to the appropriate HTTP response.
        """
        candidate = await self._resolve_candidate(user_input)
        if candidate is None:
            return None

        skill_hash = compute_skill_hash(self._registry)

        resolution = await self._verifier.verify(
            user_input=user_input,
            candidate=candidate,
            registry=self._registry,
            skill_hash=skill_hash,
            user_id=user_id,
        )

        exec_result, exec_ms, adapter_key, sql = await self._execute(resolution.effective_plan)

        provenance = build_provenance(
            resolution=resolution,
            execution_latency_ms=exec_ms,
            skill_hash=skill_hash,
            adapter_key=adapter_key,
            generated_sql=sql,
            expose_sql=self._features.expose_sql,
        )

        return ExecutedResult(
            result={
                "entity": resolution.effective_plan.entity,
                "rows": exec_result.get("rows", []),
                "totalCount": exec_result.get("total_count", 0),
                "fields": exec_result.get("fields"),
                "queryTimeMs": exec_ms,
            },
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # Candidate resolution — pattern cache first, LLM synth second
    # ------------------------------------------------------------------

    async def _resolve_candidate(
        self, user_input: str
    ) -> CandidateResolution | None:
        hit = self._pattern_cache.lookup(user_input)
        if (
            hit is not None
            and hit.confidence is not None
            and hit.confidence >= 0.90
        ):
            plan = _pattern_to_plan(
                self._pattern_cache, hit.pattern_id, hit.entity
            )
            if plan is not None:
                return CandidateResolution(
                    source="cache",
                    query_plan=plan,
                    entity=hit.entity,
                    pattern_id=hit.pattern_id,
                    pattern_match_confidence=hit.confidence,
                    intent="READ",
                )

        # LLM synthesis fallback
        if self._synthesiser is None:
            return None
        synth = await self._synthesiser.synthesise(user_input, self._registry)
        if synth is None:
            return None
        plan, confidence = synth
        return CandidateResolution(
            source="synthesised",
            query_plan=plan,
            entity=plan.entity,
            synthesis_confidence=confidence,
            intent="READ",
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(
        self, plan: QueryPlan
    ) -> tuple[dict, int, str, str | None]:
        skill = self._registry.entity_by_name.get(plan.entity)
        if skill is None:
            return {"rows": [], "total_count": 0}, 0, "unknown", None

        adapter = get_adapter_registry().get(skill.adapter)
        if adapter is None:
            return {"rows": [], "total_count": 0}, 0, skill.adapter, None

        t0 = time.monotonic()
        try:
            res = await adapter.execute_query(skill, plan)
        except Exception as exc:
            log.warning("pipeline.execute_failed", error=str(exc), entity=plan.entity)
            return {"rows": [], "total_count": 0, "error": str(exc)}, 0, skill.adapter, None
        ms = int((time.monotonic() - t0) * 1000)
        return (
            {
                "rows": res.rows,
                "total_count": res.total_count,
                "fields": None,
            },
            ms,
            skill.adapter,
            None,
        )


# ---------------------------------------------------------------------------
# Pattern-template → QueryPlan
# ---------------------------------------------------------------------------


def _pattern_to_plan(
    cache: PatternCache, pattern_id: str, entity: str
) -> QueryPlan | None:
    """Use the existing skill_registry parser — avoids duplicating the logic."""
    from backend.skill_registry.api.rest_router import _parse_pattern_template_to_plan

    pattern = cache.get_pattern(pattern_id)
    if pattern is None:
        return None
    query_template = (
        pattern.get("query_template")
        if isinstance(pattern, dict)
        else getattr(pattern, "query_template", None)
    )
    if query_template is None:
        return None
    return _parse_pattern_template_to_plan(query_template, entity)
