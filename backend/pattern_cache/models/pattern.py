"""
Pattern cache models — richer runtime models used by the cache engine.
These are separate from the skill_registry models to allow independent evolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MatchResult:
    """Result of a successful fuzzy cache lookup."""

    pattern_id: str
    confidence: float  # 0.0–1.0
    matched_trigger: str
    entity: str


@dataclass(frozen=True)
class CacheLookupResult:
    """Full result returned by PatternCache.lookup()."""

    match: MatchResult | None
    # Tier classification
    tier: str  # 'direct_execute' | 'near_miss' | 'did_you_mean' | 'cache_miss'
    query_plan: dict[str, Any] | None = None

    @property
    def pattern_id(self) -> str | None:
        return self.match.pattern_id if self.match else None

    @property
    def confidence(self) -> float | None:
        return self.match.confidence if self.match else None

    @property
    def entity(self) -> str | None:
        return self.match.entity if self.match else None

    @property
    def matched_trigger(self) -> str | None:
        return self.match.matched_trigger if self.match else None


@dataclass
class TriggerEntry:
    """Flat index entry for a single pattern trigger."""

    trigger_original: str
    trigger_normalised: str
    pattern_id: str
    entity: str


@dataclass
class CacheStats:
    """Hit rate statistics for the pattern cache."""

    total_lookups: int = 0
    hits_direct: int = 0       # confidence >= 0.95
    hits_near_miss: int = 0    # 0.90 <= confidence < 0.95
    hints_did_you_mean: int = 0  # 0.80 <= confidence < 0.90
    misses: int = 0            # confidence < 0.80

    @property
    def hit_rate(self) -> float:
        if self.total_lookups == 0:
            return 0.0
        return (self.hits_direct + self.hits_near_miss) / self.total_lookups

    @property
    def miss_rate(self) -> float:
        return 1.0 - self.hit_rate

    def to_dict(self) -> dict:
        return {
            "total_lookups": self.total_lookups,
            "hits_direct": self.hits_direct,
            "hits_near_miss": self.hits_near_miss,
            "hints_did_you_mean": self.hints_did_you_mean,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "miss_rate": round(self.miss_rate, 4),
        }
