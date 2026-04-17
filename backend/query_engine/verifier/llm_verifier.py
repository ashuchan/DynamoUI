"""LLMVerifier — core of the M3 cross-cutting change.

Decides, given a user input and a candidate resolution, whether to APPROVE
the candidate, APPROVE_WITH_NOTE, or REJECT_PREFER_LLM (in which case the
verifier also supplies its own plan + pattern-gap suggestion).

Controlled entirely by ``DYNAMO_VERIFIER_*`` env variables. When
``enabled=False`` (the default), every call short-circuits to SKIPPED and
the resolve pipeline behaves as it did before M3.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from typing import Any
from uuid import UUID

import structlog

from backend.adapters.base import QueryPlan
from backend.query_engine.verifier.cache import VerdictCache
from backend.query_engine.verifier.gap_recorder import PatternGapRecorder
from backend.query_engine.verifier.prompts import SYSTEM_PROMPT, build_user_prompt
from backend.query_engine.verifier.verdict import (
    CandidateResolution,
    PatternGapSuggestion,
    Verdict,
    VerifiedResolution,
)
from backend.skill_registry.config.settings import VerifierSettings
from backend.skill_registry.llm.provider import LLMProvider, strip_markdown_json
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)


class LLMVerifier:
    """Verifier — thin wrapper around an LLMProvider with caching + config."""

    def __init__(
        self,
        *,
        settings: VerifierSettings,
        llm_provider: LLMProvider,
        cache: VerdictCache,
        gap_recorder: PatternGapRecorder | None,
    ) -> None:
        self._settings = settings
        self._llm = llm_provider
        self._cache = cache
        self._gap = gap_recorder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def verify(
        self,
        *,
        user_input: str,
        candidate: CandidateResolution,
        registry: SkillRegistry,
        skill_hash: str,
        user_id: UUID | None = None,
    ) -> VerifiedResolution:
        """Main verifier entry point.

        Contract: always returns a VerifiedResolution whose ``effective_plan``
        is safe to execute. Never raises for ordinary LLM failures — honours
        ``on_llm_failure`` setting.
        """
        # 1. Master toggle — the env var flips the whole loop off cheaply.
        if not self._settings.enabled:
            return VerifiedResolution.skipped(candidate)

        # 2. Per-candidate policy
        if not self._should_verify(candidate):
            return VerifiedResolution.skipped(candidate)

        # 3. Verdict cache lookup
        cached = self._cache.get(user_input, candidate, skill_hash)
        if cached is not None:
            log.info(
                "verifier.cache_hit",
                verdict=cached.verdict.value,
                entity=candidate.entity,
            )
            return cached

        # 4. LLM call with timeout + graceful degradation
        t0 = time.monotonic()
        try:
            raw, cost = await asyncio.wait_for(
                self._call_llm(user_input, candidate, registry),
                timeout=self._settings.llm_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            log.warning("verifier.llm_timeout", entity=candidate.entity)
            return self._on_llm_failure(candidate, "timeout")
        except Exception as exc:
            log.warning("verifier.llm_error", error=str(exc))
            return self._on_llm_failure(candidate, str(exc))

        latency_ms = int((time.monotonic() - t0) * 1000)
        result = self._parse_verdict(
            raw,
            candidate=candidate,
            latency_ms=latency_ms,
            llm_cost_usd=cost,
        )

        # 5. On reject, record the gap asynchronously (don't block the user).
        if result.verdict == Verdict.REJECT_PREFER_LLM and self._gap is not None:
            asyncio.create_task(
                self._gap.record(
                    user_input=user_input,
                    rejected=candidate,
                    llm_plan=result.effective_plan,
                    suggestion=result.pattern_gap_suggestion,
                    user_id=user_id,
                )
            )

        self._cache.put(user_input, candidate, skill_hash, result)

        log.info(
            "verifier.verdict",
            verdict=result.verdict.value,
            candidate_source=candidate.source,
            entity=candidate.entity,
            latency_ms=latency_ms,
            cost_usd=cost,
        )
        return result

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _should_verify(self, candidate: CandidateResolution) -> bool:
        if candidate.intent in self._settings.skip_intents:
            return False
        if candidate.source == "cache" and not self._settings.verify_cache_hits:
            return False
        if candidate.source == "template" and not self._settings.verify_templates:
            return False
        if candidate.source == "synthesised" and not self._settings.verify_synthesised:
            return False
        if candidate.source == "saved_view" and not self._settings.verify_saved_views:
            return False
        # Very-high-confidence cache hits can skip verification.
        conf = candidate.pattern_match_confidence
        if (
            candidate.source == "cache"
            and conf is not None
            and conf >= self._settings.skip_on_confidence_above
        ):
            return False
        return True

    def _on_llm_failure(
        self, candidate: CandidateResolution, reason: str
    ) -> VerifiedResolution:
        mode = self._settings.on_llm_failure
        if mode == "approve_candidate":
            return VerifiedResolution.errored(
                candidate, error=reason, fallback_to_candidate=True
            )
        # "reject_and_synth" — currently degrades to approve since we have no
        # independent synth path inside the verifier. Caller decides next step.
        return VerifiedResolution.errored(
            candidate, error=reason, fallback_to_candidate=True
        )

    # ------------------------------------------------------------------
    # LLM mechanics
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        user_input: str,
        candidate: CandidateResolution,
        registry: SkillRegistry,
    ) -> tuple[str, float]:
        skill_excerpt = _build_skill_excerpt(candidate.query_plan, registry)
        user_prompt = build_user_prompt(
            user_input=user_input,
            plan=candidate.query_plan,
            candidate_source=candidate.source,
            skill_excerpt=skill_excerpt,
        )
        response = await self._llm.complete(SYSTEM_PROMPT, user_prompt)
        cost = _estimate_cost_usd(response)
        return response.text, cost

    def _parse_verdict(
        self,
        raw: str,
        *,
        candidate: CandidateResolution,
        latency_ms: int,
        llm_cost_usd: float,
    ) -> VerifiedResolution:
        if not raw:
            return VerifiedResolution.errored(
                candidate, error="empty_llm_response", fallback_to_candidate=True
            )
        try:
            data = json.loads(strip_markdown_json(raw))
        except json.JSONDecodeError:
            return VerifiedResolution.errored(
                candidate, error="unparseable_verdict", fallback_to_candidate=True
            )

        verdict_raw = str(data.get("verdict", "approve")).lower()
        note = data.get("reason")

        if verdict_raw == "reject":
            from backend.skill_registry.llm.query_synthesiser import _parse_query_plan

            plan_data = data.get("llm_plan") or {}
            try:
                llm_plan = _parse_query_plan(plan_data)
            except Exception as exc:
                log.warning("verifier.bad_llm_plan", error=str(exc))
                return VerifiedResolution.errored(
                    candidate, error=f"bad_llm_plan:{exc}", fallback_to_candidate=True
                )

            gap = _parse_gap_suggestion(data.get("pattern_gap_suggestion"))
            return VerifiedResolution.rerouted(
                candidate,
                llm_plan=llm_plan,
                note=note,
                gap=gap,
                latency_ms=latency_ms,
                llm_cost_usd=llm_cost_usd,
            )

        # approve / approve_with_note
        return VerifiedResolution.approved(
            candidate,
            verified=True,
            note=note if verdict_raw == "approve_with_note" else None,
            latency_ms=latency_ms,
            llm_cost_usd=llm_cost_usd,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_skill_excerpt(plan: QueryPlan, registry: SkillRegistry) -> str:
    """Only send schema for entities referenced in the plan — keeps prompt cheap."""
    entities = {plan.entity} | {j.target_entity for j in plan.joins}
    lines: list[str] = []
    for name in sorted(entities):
        skill = registry.entity_by_name.get(name)
        if skill is None:
            continue
        non_sensitive = [f for f in skill.fields if not f.sensitive]
        parts = []
        for f in non_sensitive:
            suffix = " PK" if f.isPK else ""
            parts.append(f"{f.name} ({f.type}{suffix})")
        lines.append(f"{name}(table={skill.table}): {', '.join(parts)}")
    return "\n".join(lines)


def _parse_gap_suggestion(data: Any) -> PatternGapSuggestion | None:
    if not isinstance(data, dict):
        return None
    st = data.get("suggestion_type")
    if st not in ("add_trigger", "new_pattern", "refine_description"):
        return None
    return PatternGapSuggestion(
        suggestion_type=st,
        target_pattern_id=data.get("target_pattern_id"),
        proposed_nl_trigger=data.get("proposed_nl_trigger"),
        proposed_pattern_body=data.get("proposed_pattern_body"),
    )


def _estimate_cost_usd(response) -> float:
    """Rough estimate — MeteringLLMProvider records the authoritative value."""
    # Haiku pricing fallback — $0.80/$4.00 per 1M tokens as of Jan 2026.
    prompt = getattr(response, "prompt_tokens", 0) or 0
    completion = getattr(response, "completion_tokens", 0) or 0
    return (prompt * 0.0000008) + (completion * 0.000004)
