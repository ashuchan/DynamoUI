"""
Unit tests for pattern_cache module:
- PatternHasher
- TriggerIndex
- FuzzyMatcher
- PatternCache (lookup, tier classification, stats)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.pattern_cache.cache.pattern_cache import PatternCache
from backend.pattern_cache.index.fuzzy_matcher import FuzzyMatcher
from backend.pattern_cache.index.trigger_index import TriggerIndex
from backend.pattern_cache.models.pattern import TriggerEntry
from backend.pattern_cache.versioning.hasher import PatternHasher
from backend.skill_registry.models.pattern import PatternFile, Pattern


STOPWORDS = ["the", "a", "an", "all", "show", "me", "get", "find", "list", "please", "can", "you"]


# ---------------------------------------------------------------------------
# PatternHasher tests
# ---------------------------------------------------------------------------


class TestPatternHasher:
    def test_compute_skill_hash_length(self, tmp_path):
        skill_file = tmp_path / "test.skill.yaml"
        skill_file.write_text("entity: Test\ntable: test\n", encoding="utf-8")
        h = PatternHasher.compute_skill_hash(skill_file)
        assert len(h) == 16

    def test_compute_skill_hash_is_deterministic(self, tmp_path):
        skill_file = tmp_path / "test.skill.yaml"
        skill_file.write_text("entity: Test\ntable: test\n", encoding="utf-8")
        h1 = PatternHasher.compute_skill_hash(skill_file)
        h2 = PatternHasher.compute_skill_hash(skill_file)
        assert h1 == h2

    def test_verify_matching_hash(self, tmp_path):
        skill_file = tmp_path / "entity.skill.yaml"
        skill_file.write_text("entity: Test\ntable: test\n", encoding="utf-8")
        h = PatternHasher.compute_skill_hash(skill_file)

        pattern_file = tmp_path / "entity.patterns.yaml"
        pattern_file.write_text(
            f"# skill_hash: {h}\nentity: Test\npatterns: []\n",
            encoding="utf-8",
        )
        assert PatternHasher.verify(pattern_file, skill_file) is True

    def test_verify_stale_hash_returns_false(self, tmp_path):
        skill_file = tmp_path / "entity.skill.yaml"
        skill_file.write_text("entity: Test\ntable: test\n", encoding="utf-8")

        pattern_file = tmp_path / "entity.patterns.yaml"
        pattern_file.write_text(
            "# skill_hash: stalehashvalue1\nentity: Test\npatterns: []\n",
            encoding="utf-8",
        )
        assert PatternHasher.verify(pattern_file, skill_file) is False

    def test_verify_missing_header_returns_false(self, tmp_path):
        skill_file = tmp_path / "entity.skill.yaml"
        skill_file.write_text("entity: Test\n", encoding="utf-8")

        pattern_file = tmp_path / "entity.patterns.yaml"
        pattern_file.write_text("entity: Test\npatterns: []\n", encoding="utf-8")
        assert PatternHasher.verify(pattern_file, skill_file) is False

    def test_custom_hash_length(self, tmp_path):
        skill_file = tmp_path / "test.skill.yaml"
        skill_file.write_text("content", encoding="utf-8")
        h = PatternHasher.compute_skill_hash(skill_file, length=8)
        assert len(h) == 8


# ---------------------------------------------------------------------------
# TriggerIndex tests
# ---------------------------------------------------------------------------


class TestTriggerIndex:
    def _make_entries(self) -> list[TriggerEntry]:
        return [
            TriggerEntry("active employees", "active employees", "employee.active", "Employee"),
            TriggerEntry("contractors", "contractors", "employee.contractors", "Employee"),
            TriggerEntry("invoices unpaid", "invoices unpaid", "invoice.unpaid", "Invoice"),
        ]

    def test_build_and_lookup_all_entries(self):
        idx = TriggerIndex(stopwords=STOPWORDS)
        idx.build(self._make_entries())
        assert idx.total_triggers == 3

    def test_entity_scoped_lookup(self):
        idx = TriggerIndex(stopwords=STOPWORDS)
        idx.build(self._make_entries())
        entries = idx.entries_for_entity("Employee")
        assert len(entries) == 2
        assert all(e.entity == "Employee" for e in entries)

    def test_empty_entity_returns_empty_list(self):
        idx = TriggerIndex(stopwords=STOPWORDS)
        idx.build(self._make_entries())
        assert idx.entries_for_entity("Nonexistent") == []

    def test_normalise_removes_stopwords(self):
        idx = TriggerIndex(stopwords=STOPWORDS)
        result = idx.normalise("show me all active employees please")
        assert "show" not in result
        assert "me" not in result
        assert "all" not in result
        assert "active" in result
        assert "employees" in result

    def test_normalise_lowercases(self):
        idx = TriggerIndex(stopwords=STOPWORDS)
        result = idx.normalise("ACTIVE Employees")
        assert result == result.lower()


# ---------------------------------------------------------------------------
# FuzzyMatcher tests
# ---------------------------------------------------------------------------


class TestFuzzyMatcher:
    def _make_cache(self, threshold=0.90) -> tuple[FuzzyMatcher, TriggerIndex]:
        entries = [
            TriggerEntry("active employees", "active employees", "employee.active", "Employee"),
            TriggerEntry("employees in department", "employees department", "employee.by_department", "Employee"),
            TriggerEntry("contractors", "contractors", "employee.contractors", "Employee"),
        ]
        idx = TriggerIndex(stopwords=STOPWORDS)
        idx.build(entries)
        matcher = FuzzyMatcher(idx, threshold=threshold)
        return matcher, idx

    def test_exact_match_returns_high_confidence(self):
        matcher, _ = self._make_cache()
        result = matcher.match("active employees")
        assert result is not None
        assert result.pattern_id == "employee.active"
        assert result.confidence >= 0.90

    def test_near_match_returns_result(self):
        matcher, _ = self._make_cache(threshold=0.70)
        result = matcher.match("show active employees")
        assert result is not None

    def test_no_match_returns_none(self):
        matcher, _ = self._make_cache()
        result = matcher.match("completely unrelated xyz query 12345")
        assert result is None

    def test_entity_scoped_match(self):
        matcher, _ = self._make_cache()
        result = matcher.match("contractors", entity_hint="Employee")
        assert result is not None
        assert result.entity == "Employee"

    def test_confidence_is_between_0_and_1(self):
        matcher, _ = self._make_cache(threshold=0.0)
        result = matcher.match("active employees")
        assert result is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_score_conversion_from_rapidfuzz_range(self):
        """Confidence must be 0.0–1.0, not 0–100."""
        matcher, _ = self._make_cache(threshold=0.0)
        result = matcher.match("active employees")
        assert result is not None
        assert result.confidence <= 1.0, "Confidence must be 0–1, not 0–100 (RapidFuzz raw score)"


# ---------------------------------------------------------------------------
# PatternCache tier classification tests
# ---------------------------------------------------------------------------


class TestPatternCacheTiers:
    def test_lookup_direct_execute_tier(self, built_pattern_cache):
        result = built_pattern_cache.lookup("active employees")
        assert result is not None
        assert result.tier in ("direct_execute", "near_miss")
        assert result.confidence >= 0.90

    def test_lookup_cache_miss_returns_miss_tier(self, built_pattern_cache):
        result = built_pattern_cache.lookup("zzz xyzzy 999")
        assert result is not None
        assert result.tier == "cache_miss"
        assert result.match is None

    def test_stats_increment_on_lookup(self, built_pattern_cache):
        initial = built_pattern_cache.stats()["total_lookups"]
        built_pattern_cache.lookup("active employees")
        built_pattern_cache.lookup("zzz xyzzy 999")
        after = built_pattern_cache.stats()["total_lookups"]
        assert after == initial + 2

    def test_get_pattern_by_id(self, built_pattern_cache):
        pattern = built_pattern_cache.get_pattern("employee.active")
        assert pattern is not None
        assert pattern["id"] == "employee.active"

    def test_get_pattern_not_found(self, built_pattern_cache):
        assert built_pattern_cache.get_pattern("nonexistent.pattern") is None

    def test_patterns_for_entity(self, built_pattern_cache):
        patterns = built_pattern_cache.patterns_for_entity("Employee")
        assert len(patterns) == 3

    def test_patterns_for_unknown_entity(self, built_pattern_cache):
        assert built_pattern_cache.patterns_for_entity("Nonexistent") == []

    def test_stats_hit_rate(self, built_pattern_cache):
        built_pattern_cache.lookup("active employees")
        built_pattern_cache.lookup("contractors")
        stats = built_pattern_cache.stats()
        assert 0.0 <= stats["hit_rate"] <= 1.0

    def test_entity_hint_scopes_lookup(self, built_pattern_cache):
        result = built_pattern_cache.lookup("contractors", entity_hint="Employee")
        assert result is not None
        if result.match:
            assert result.match.entity == "Employee"


# ---------------------------------------------------------------------------
# Performance test
# ---------------------------------------------------------------------------


def test_cache_lookup_performance_5000_triggers():
    """
    Pattern cache lookup with 5,000 triggers must complete in < 5ms.
    Performance gate per the LLD.
    """
    import time
    from backend.skill_registry.models.pattern import Pattern, PatternFile

    # Build a PatternFile with 5000 triggers across many patterns
    patterns = []
    trigger_count = 0
    pattern_idx = 0
    while trigger_count < 5000:
        triggers = [f"query number {trigger_count + i} for test" for i in range(10)]
        patterns.append(
            Pattern(
                id=f"perf.pattern_{pattern_idx}",
                description="",
                triggers=triggers,
                query_template="{}",
            )
        )
        trigger_count += 10
        pattern_idx += 1

    pf = PatternFile(skill_hash="abc123abc123abc1", entity="PerfEntity", patterns=patterns)
    cache = PatternCache(threshold=0.90, enforce_skill_hash=False)
    cache.build_from_pattern_files([pf])

    # Warm up
    cache.lookup("query number 100 for test")

    # Measure
    t0 = time.perf_counter()
    cache.lookup("query number 2500 for test")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 5.0, (
        f"Cache lookup took {elapsed_ms:.2f}ms — must be < 5ms with 5000 triggers"
    )
