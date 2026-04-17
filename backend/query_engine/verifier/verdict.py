"""Verdict dataclasses and enums for the LLM verification loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from backend.adapters.base import QueryPlan


class Verdict(str, Enum):
    APPROVE = "approve"
    REJECT_PREFER_LLM = "reject"
    APPROVE_WITH_NOTE = "approve_with_note"
    SKIPPED = "skipped"
    ERROR = "error"


CandidateSource = Literal["cache", "template", "synthesised", "saved_view"]


@dataclass
class CandidateResolution:
    """The candidate produced by upstream resolution, fed into the verifier."""

    source: CandidateSource
    query_plan: QueryPlan
    entity: str
    pattern_id: str | None = None
    pattern_match_confidence: float | None = None
    synthesis_confidence: float | None = None
    intent: str = "READ"


@dataclass
class PatternGapSuggestion:
    """Structured hint for closing a pattern gap when the verifier rejects."""

    suggestion_type: Literal["add_trigger", "new_pattern", "refine_description"]
    target_pattern_id: str | None = None
    proposed_nl_trigger: str | None = None
    proposed_pattern_body: dict[str, Any] | None = None


@dataclass
class VerifiedResolution:
    """The result of the verifier — never mutates the candidate in place."""

    verdict: Verdict
    effective_plan: QueryPlan            # plan that should actually be executed
    original_candidate: CandidateResolution
    verified: bool = False               # True iff verifier actually ran
    note: str | None = None
    rerouted_plan: QueryPlan | None = None   # only on REJECT
    pattern_gap_suggestion: PatternGapSuggestion | None = None
    latency_ms: int = 0
    cache_hit: bool = False
    llm_cost_usd: float = 0.0
    error: str | None = None

    @classmethod
    def skipped(cls, candidate: CandidateResolution) -> "VerifiedResolution":
        return cls(
            verdict=Verdict.SKIPPED,
            effective_plan=candidate.query_plan,
            original_candidate=candidate,
            verified=False,
        )

    @classmethod
    def approved(
        cls,
        candidate: CandidateResolution,
        *,
        verified: bool,
        note: str | None = None,
        latency_ms: int = 0,
        cache_hit: bool = False,
        llm_cost_usd: float = 0.0,
    ) -> "VerifiedResolution":
        return cls(
            verdict=Verdict.APPROVE_WITH_NOTE if note else Verdict.APPROVE,
            effective_plan=candidate.query_plan,
            original_candidate=candidate,
            verified=verified,
            note=note,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            llm_cost_usd=llm_cost_usd,
        )

    @classmethod
    def rerouted(
        cls,
        candidate: CandidateResolution,
        *,
        llm_plan: QueryPlan,
        note: str | None,
        gap: PatternGapSuggestion | None,
        latency_ms: int = 0,
        llm_cost_usd: float = 0.0,
    ) -> "VerifiedResolution":
        return cls(
            verdict=Verdict.REJECT_PREFER_LLM,
            effective_plan=llm_plan,
            rerouted_plan=llm_plan,
            original_candidate=candidate,
            verified=True,
            note=note,
            pattern_gap_suggestion=gap,
            latency_ms=latency_ms,
            llm_cost_usd=llm_cost_usd,
        )

    @classmethod
    def errored(
        cls,
        candidate: CandidateResolution,
        *,
        error: str,
        fallback_to_candidate: bool,
    ) -> "VerifiedResolution":
        return cls(
            verdict=Verdict.ERROR,
            effective_plan=candidate.query_plan,
            original_candidate=candidate,
            verified=False,
            error=error,
        )
