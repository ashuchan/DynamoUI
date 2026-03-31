"""
PatternCache — facade that combines TriggerIndex + FuzzyMatcher + stats tracking.

Confidence tiers (primary LLM-call control):
  >= 0.95  → direct execute, no confirmation, no LLM
  0.90–0.94 → direct execute, log as near-miss, no LLM
  0.80–0.89 → "Did you mean…?" prompt, no LLM (unless user rejects)
  < 0.80   → cache miss → LLM fallback (Phase 2)

Do NOT change thresholds without an explicit design decision.
"""
from __future__ import annotations

import time
from pathlib import Path

import structlog

from backend.pattern_cache.index.fuzzy_matcher import FuzzyMatcher
from backend.pattern_cache.index.trigger_index import TriggerIndex
from backend.pattern_cache.loader.pattern_loader import PatternLoader
from backend.pattern_cache.models.pattern import (
    CacheLookupResult,
    CacheStats,
    MatchResult,
    TriggerEntry,
)
from backend.skill_registry.models.pattern import PatternFile

log = structlog.get_logger(__name__)


class PatternCache:
    """
    Runtime pattern cache. Built once at startup from *.patterns.yaml files.
    Thread-safe for read operations (index is read-only in Phase 1).
    """

    # Confidence tier thresholds — locked by LLD. Do not modify.
    TIER_DIRECT_EXECUTE = 0.95
    TIER_NEAR_MISS_LOWER = 0.90
    TIER_DID_YOU_MEAN_LOWER = 0.80

    def __init__(
        self,
        threshold: float = 0.90,
        stopwords: list[str] | None = None,
        enforce_skill_hash: bool = True,
        hash_length: int = 16,
    ) -> None:
        self._threshold = threshold
        self._stopwords = stopwords or [
            "the", "a", "an", "all", "show", "me", "get",
            "find", "list", "please", "can", "you",
        ]
        self._enforce_skill_hash = enforce_skill_hash
        self._hash_length = hash_length

        self._index = TriggerIndex(self._stopwords)
        self._matcher = FuzzyMatcher(self._index, threshold=threshold)
        self._stats = CacheStats()
        self._pattern_files: list[PatternFile] = []
        # pattern_id -> PatternFile for reverse lookup
        self._pattern_by_id: dict[str, PatternFile] = {}

        self._last_stats_log: float = time.monotonic()

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def build(self, skills_dir: Path, *, extra_dirs: list[Path] | None = None) -> None:
        """
        Discover, load, and index all *.patterns.yaml files.
        Called once at startup before serving requests.
        """
        loader = PatternLoader(
            enforce_skill_hash=self._enforce_skill_hash,
            hash_length=self._hash_length,
        )
        pattern_files, errors = loader.load_all(skills_dir, extra_dirs=extra_dirs)
        self._pattern_files = pattern_files

        trigger_entries = loader.build_trigger_entries(pattern_files, self._stopwords)
        self._index.build(trigger_entries)

        self._matcher = FuzzyMatcher(self._index, threshold=self._threshold)

        for pf in pattern_files:
            for p in pf.patterns:
                self._pattern_by_id[p.id] = pf

        log.info(
            "pattern_cache.built",
            pattern_files=len(pattern_files),
            total_triggers=self._index.total_triggers,
            errors=len(errors),
        )

    def build_from_pattern_files(self, pattern_files: list[PatternFile]) -> None:
        """Build directly from pre-loaded PatternFile objects (used in tests)."""
        self._pattern_files = pattern_files
        loader = PatternLoader(enforce_skill_hash=False)
        trigger_entries = loader.build_trigger_entries(pattern_files, self._stopwords)
        self._index.build(trigger_entries)
        self._matcher = FuzzyMatcher(self._index, threshold=self._threshold)
        for pf in pattern_files:
            for p in pf.patterns:
                self._pattern_by_id[p.id] = pf

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(
        self, user_input: str, entity_hint: str | None = None
    ) -> CacheLookupResult | None:
        """
        Perform a fuzzy cache lookup.

        Returns CacheLookupResult with tier classification, or None on hard miss.
        Increments internal stats for observability.
        """
        self._stats.total_lookups += 1

        match = self._matcher.match(user_input, entity_hint=entity_hint)

        self._maybe_log_stats()

        if match is None or match.confidence < self.TIER_DID_YOU_MEAN_LOWER:
            self._stats.misses += 1
            log.debug("pattern_cache.miss", input_len=len(user_input))
            return CacheLookupResult(match=None, tier="cache_miss")

        if match.confidence >= self.TIER_DIRECT_EXECUTE:
            self._stats.hits_direct += 1
            tier = "direct_execute"
            log.debug(
                "pattern_cache.hit_direct",
                pattern_id=match.pattern_id,
                confidence=match.confidence,
            )
        elif match.confidence >= self.TIER_NEAR_MISS_LOWER:
            self._stats.hits_near_miss += 1
            tier = "near_miss"
            log.info(
                "pattern_cache.near_miss",
                pattern_id=match.pattern_id,
                confidence=match.confidence,
            )
        else:
            self._stats.hints_did_you_mean += 1
            tier = "did_you_mean"
            log.debug(
                "pattern_cache.did_you_mean",
                pattern_id=match.pattern_id,
                confidence=match.confidence,
            )

        return CacheLookupResult(match=match, tier=tier)

    # ------------------------------------------------------------------
    # Pattern access
    # ------------------------------------------------------------------

    def get_pattern(self, pattern_id: str) -> dict | None:
        """Return the full pattern definition as a dict, or None if not found."""
        pf = self._pattern_by_id.get(pattern_id)
        if pf is None:
            return None
        for p in pf.patterns:
            if p.id == pattern_id:
                return p.model_dump()
        return None

    def patterns_for_entity(self, entity: str) -> list[dict]:
        """Return all patterns registered for an entity."""
        pf = next((p for p in self._pattern_files if p.entity == entity), None)
        if pf is None:
            return []
        return [p.model_dump() for p in pf.patterns]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return self._stats.to_dict()

    def _maybe_log_stats(self, interval_seconds: int = 300) -> None:
        """Periodically emit stats to the log."""
        now = time.monotonic()
        if now - self._last_stats_log >= interval_seconds:
            log.info("pattern_cache.stats", **self._stats.to_dict())
            self._last_stats_log = now
