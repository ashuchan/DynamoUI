"""In-memory LRU verdict cache.

Key: sha256(normalised_input + plan_hash + skill_registry_hash)
Value: VerifiedResolution
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict

from backend.adapters.base import QueryPlan
from backend.query_engine.verifier.verdict import (
    CandidateResolution,
    VerifiedResolution,
)


def _plan_hash(plan: QueryPlan) -> str:
    data = {
        "entity": plan.entity,
        "filters": [[f.field, f.op, f.value] for f in plan.filters],
        "sort": [[s.field, s.dir] for s in plan.sort],
        "joins": [[j.source_field, j.target_entity, j.target_field, j.join_type] for j in plan.joins],
        "aggregations": [[a.func, a.field, a.alias] for a in plan.aggregations],
        "group_by": plan.group_by,
        "result_limit": plan.result_limit,
        "page": plan.page,
        "page_size": plan.page_size,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()


def _normalise(s: str) -> str:
    return " ".join(s.lower().strip().split())


class VerdictCache:
    """Simple thread-unsafe LRU. Good enough for single-process phase 2."""

    def __init__(self, max_size: int = 2048) -> None:
        self._max_size = max(1, max_size)
        self._store: OrderedDict[str, VerifiedResolution] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _key(
        self, user_input: str, candidate: CandidateResolution, skill_hash: str
    ) -> str:
        body = f"{_normalise(user_input)}|{_plan_hash(candidate.query_plan)}|{skill_hash}"
        return hashlib.sha256(body.encode()).hexdigest()

    def get(
        self, user_input: str, candidate: CandidateResolution, skill_hash: str
    ) -> VerifiedResolution | None:
        key = self._key(user_input, candidate, skill_hash)
        v = self._store.get(key)
        if v is None:
            self._misses += 1
            return None
        self._store.move_to_end(key)
        self._hits += 1
        # Mark as cache hit on read — the returned object is shared; fine for reads
        cached = VerifiedResolution(**{**asdict(v), "cache_hit": True})
        cached.effective_plan = v.effective_plan  # asdict does not round-trip dataclasses-in-fields
        cached.original_candidate = v.original_candidate
        cached.rerouted_plan = v.rerouted_plan
        cached.pattern_gap_suggestion = v.pattern_gap_suggestion
        return cached

    def put(
        self,
        user_input: str,
        candidate: CandidateResolution,
        skill_hash: str,
        resolution: VerifiedResolution,
    ) -> None:
        key = self._key(user_input, candidate, skill_hash)
        self._store[key] = resolution
        self._store.move_to_end(key)
        while len(self._store) > self._max_size:
            self._store.popitem(last=False)

    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
        }
