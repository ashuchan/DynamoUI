"""
TriggerIndex — flat index of normalised_trigger -> TriggerEntry.
Supports entity-scoped lookup for precision matching.
"""
from __future__ import annotations

import re

import structlog

from backend.pattern_cache.models.pattern import TriggerEntry

log = structlog.get_logger(__name__)


class TriggerIndex:
    """
    In-memory flat index of all pattern triggers.
    Built once at startup; read-only thereafter (Phase 1).
    """

    def __init__(self, stopwords: list[str]) -> None:
        self._stopwords = stopwords
        self._entries: list[TriggerEntry] = []
        # entity -> list of entries (for entity-scoped matching)
        self._by_entity: dict[str, list[TriggerEntry]] = {}
        # pattern_id -> list of entries (for pattern lookup)
        self._by_pattern_id: dict[str, list[TriggerEntry]] = {}

    def build(self, entries: list[TriggerEntry]) -> None:
        """Populate the index from a list of TriggerEntry objects."""
        self._entries = list(entries)
        self._by_entity = {}
        self._by_pattern_id = {}

        for entry in self._entries:
            self._by_entity.setdefault(entry.entity, []).append(entry)
            self._by_pattern_id.setdefault(entry.pattern_id, []).append(entry)

        log.info(
            "trigger_index.built",
            total_triggers=len(self._entries),
            entities=len(self._by_entity),
            patterns=len(self._by_pattern_id),
        )

    def normalise(self, text: str) -> str:
        """Lowercase, strip punctuation, remove stopwords."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        tokens = text.split()
        tokens = [t for t in tokens if t not in self._stopwords]
        return " ".join(tokens)

    # Keep private alias used by FuzzyMatcher (spec-compatible)
    _normalise = normalise

    def entries_for_entity(self, entity: str) -> list[TriggerEntry]:
        return self._by_entity.get(entity, [])

    def all_entries(self) -> list[TriggerEntry]:
        return self._entries

    def entries_for_pattern(self, pattern_id: str) -> list[TriggerEntry]:
        return self._by_pattern_id.get(pattern_id, [])

    @property
    def total_triggers(self) -> int:
        return len(self._entries)

    @property
    def entity_names(self) -> list[str]:
        return list(self._by_entity.keys())
