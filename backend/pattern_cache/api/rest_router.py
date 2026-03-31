"""
Pattern cache REST endpoints.
All prefixed with /api/v1 by the main app.
"""
from __future__ import annotations

import hashlib
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.pattern_cache.cache.pattern_cache import PatternCache

log = structlog.get_logger(__name__)

router = APIRouter()


def get_cache(request: Request) -> PatternCache:
    cache: PatternCache = getattr(request.app.state, "pattern_cache", None)
    if cache is None:
        raise HTTPException(status_code=503, detail="Pattern cache not initialised")
    return cache


CacheDep = Annotated[PatternCache, Depends(get_cache)]


class MatchRequest(BaseModel):
    input: str
    entity_hint: str | None = None


@router.post("/patterns/match", summary="Fuzzy match user input against pattern cache")
def match_pattern(body: MatchRequest, cache: CacheDep) -> dict:
    if len(body.input) > 500:
        raise HTTPException(status_code=400, detail="Input exceeds 500 character limit")

    input_hash = hashlib.sha256(body.input.encode()).hexdigest()[:16]
    log.info(
        "api.patterns.match",
        input_hash=input_hash,
        entity_hint=body.entity_hint,
    )

    result = cache.lookup(body.input, entity_hint=body.entity_hint)
    if result is None or result.tier == "cache_miss":
        return {"hit": False, "tier": "cache_miss"}

    return {
        "hit": True,
        "tier": result.tier,
        "pattern_id": result.pattern_id,
        "confidence": result.confidence,
        "matched_trigger": result.matched_trigger,
        "entity": result.entity,
    }


@router.get("/patterns/stats", summary="Pattern cache hit/miss statistics")
def pattern_stats(cache: CacheDep) -> dict:
    return cache.stats()


@router.get("/patterns/entity/{entity}", summary="All patterns for an entity")
def patterns_for_entity(entity: str, cache: CacheDep) -> list[dict]:
    patterns = cache.patterns_for_entity(entity)
    if not patterns:
        raise HTTPException(status_code=404, detail=f"No patterns found for entity {entity!r}")
    return patterns


@router.get("/patterns/{pattern_id}", summary="Full pattern definition")
def get_pattern(pattern_id: str, cache: CacheDep) -> dict:
    pattern = cache.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id!r} not found")
    return pattern
