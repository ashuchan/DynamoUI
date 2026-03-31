"""
FuzzyMatcher — RapidFuzz matching engine for pattern cache lookups.
Uses fuzz.token_sort_ratio with score_cutoff in 0–100 range.
Confidence is stored and returned as 0.0–1.0.

IMPORTANT: RapidFuzz scores are 0–100, NOT 0–1.
  - Threshold stored as 0.0–1.0 (e.g. 0.90)
  - score_cutoff passed to RapidFuzz as threshold * 100 (e.g. 90)
  - Result score divided by 100 before storage
"""
from __future__ import annotations

import structlog
from rapidfuzz import fuzz, process

from backend.pattern_cache.index.trigger_index import TriggerIndex
from backend.pattern_cache.models.pattern import MatchResult

log = structlog.get_logger(__name__)


class FuzzyMatcher:
    """
    Wraps RapidFuzz to perform fuzzy trigger matching against the TriggerIndex.
    Follows the canonical implementation from the LLD exactly.
    """

    def __init__(self, index: TriggerIndex, threshold: float = 0.9) -> None:
        # threshold stored as 0.0–1.0
        self._index = index
        self._threshold = threshold

    def match(
        self, user_input: str, entity_hint: str | None = None
    ) -> MatchResult | None:
        """
        Fuzzy-match user_input against the trigger index.

        - If entity_hint provided: scopes search to that entity's triggers only.
        - Returns None if no match meets the threshold.
        - Confidence is 0.0–1.0 (RapidFuzz score / 100).
        """
        normalised = self._index._normalise(user_input)

        if not normalised:
            log.debug("fuzzy_matcher.empty_normalised", raw=user_input[:50])
            return None

        entries = (
            self._index.entries_for_entity(entity_hint)
            if entity_hint
            else self._index.all_entries()
        )

        if not entries:
            log.debug(
                "fuzzy_matcher.no_entries",
                entity_hint=entity_hint,
                total=self._index.total_triggers,
            )
            return None

        choices = [e.trigger_normalised for e in entries]

        # score_cutoff is 0–100 (RapidFuzz range) — NOT 0–1
        result = process.extractOne(
            normalised,
            choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=self._threshold * 100,
        )

        if result is None:
            log.debug(
                "fuzzy_matcher.no_match",
                normalised=normalised[:50],
                threshold=self._threshold,
            )
            return None

        matched_text, score, idx = result
        entry = entries[idx]

        # Convert back to 0.0–1.0 for storage
        confidence = score / 100.0

        log.debug(
            "fuzzy_matcher.match",
            pattern_id=entry.pattern_id,
            confidence=confidence,
            normalised=normalised[:50],
            matched=matched_text[:50],
        )

        return MatchResult(
            pattern_id=entry.pattern_id,
            confidence=confidence,
            matched_trigger=entry.trigger_original,
            entity=entry.entity,
        )

    def match_many(
        self,
        user_input: str,
        entity_hint: str | None = None,
        limit: int = 5,
    ) -> list[MatchResult]:
        """Return top-N matches above threshold (used for 'did you mean' suggestions)."""
        normalised = self._index._normalise(user_input)
        if not normalised:
            return []

        entries = (
            self._index.entries_for_entity(entity_hint)
            if entity_hint
            else self._index.all_entries()
        )
        if not entries:
            return []

        choices = [e.trigger_normalised for e in entries]
        raw_results = process.extract(
            normalised,
            choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=self._threshold * 100,
            limit=limit,
        )

        results = []
        for matched_text, score, idx in raw_results:
            entry = entries[idx]
            results.append(
                MatchResult(
                    pattern_id=entry.pattern_id,
                    confidence=score / 100.0,
                    matched_trigger=entry.trigger_original,
                    entity=entry.entity,
                )
            )

        return results
