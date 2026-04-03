"""
FastAPI router for the Skill Registry module.
Covers: /enums, /schema, /widgets, /mutate endpoints.
All routes are prefixed with /api/v1 by the main app.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from backend.skill_registry.formatters.llm_formatter import (
    format_enum_for_llm,
    format_entity_for_llm,
)
from backend.skill_registry.formatters.ui_formatter import (
    format_display_config,
    format_enum_full,
    format_enum_options,
    format_field_meta,
    format_mutation_defs,
)
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency — fetch SkillRegistry from app state
# ---------------------------------------------------------------------------


def get_registry(request: Request) -> SkillRegistry:
    registry: SkillRegistry = request.app.state.skill_registry
    return registry


RegistryDep = Annotated[SkillRegistry, Depends(get_registry)]


# ---------------------------------------------------------------------------
# Enum endpoints
# ---------------------------------------------------------------------------


@router.get("/enums", summary="List all registered enums")
def list_enums(registry: RegistryDep) -> list[dict]:
    log.debug("api.enums.list", count=registry.enums_loaded)
    return [
        {"name": e.name, "description": e.description, "group": e.group}
        for e in registry.enum_by_name.values()
    ]


@router.get("/enums/by-group/{group}", summary="Enums in a group")
def enums_by_group(group: str, registry: RegistryDep) -> list[dict]:
    from backend.skill_registry.registry.enum_registry import EnumRegistry
    enum_reg: EnumRegistry = getattr(registry, "_enum_registry", None)
    if enum_reg:
        enums = enum_reg.by_group(group)
    else:
        enums = [e for e in registry.enum_by_name.values() if e.group == group]
    if not enums:
        raise HTTPException(status_code=404, detail=f"No enums found in group {group!r}")
    return [format_enum_full(e) for e in enums]


@router.get("/enums/{name}", summary="Full enum definition")
def get_enum(name: str, registry: RegistryDep) -> dict:
    enum = registry.enum_by_name.get(name)
    if enum is None:
        raise HTTPException(status_code=404, detail=f"Enum {name!r} not found")
    log.debug("api.enums.get", name=name)
    return format_enum_full(enum)


@router.get("/enums/{name}/options", summary="UI-ready dropdown options")
def enum_options(name: str, registry: RegistryDep, mode: str = "create") -> dict:
    enum = registry.enum_by_name.get(name)
    if enum is None:
        raise HTTPException(status_code=404, detail=f"Enum {name!r} not found")
    if mode not in ("create", "edit", "filter"):
        raise HTTPException(
            status_code=400,
            detail="mode must be one of: create, edit, filter",
        )
    return format_enum_options(enum, mode=mode)


@router.get("/enums/{name}/llm-context", summary="LLM-formatted plain text enum context")
def enum_llm_context(name: str, registry: RegistryDep) -> dict:
    enum = registry.enum_by_name.get(name)
    if enum is None:
        raise HTTPException(status_code=404, detail=f"Enum {name!r} not found")
    return {"name": name, "context": format_enum_for_llm(enum)}


# ---------------------------------------------------------------------------
# Schema endpoints
# ---------------------------------------------------------------------------


@router.get("/schema/{entity}/display", summary="DisplayConfig for entity")
def entity_display_config(entity: str, registry: RegistryDep) -> dict:
    skill = registry.entity_by_name.get(entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity!r} not found")
    return format_display_config(skill)


@router.get("/schema/{entity}/fields", summary="FieldMeta[] for entity")
def entity_fields(entity: str, registry: RegistryDep) -> list[dict]:
    skill = registry.entity_by_name.get(entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity!r} not found")
    return [format_field_meta(f) for f in skill.fields]


@router.get("/schema/{entity}/mutations", summary="MutationDef[] for entity")
def entity_mutations(entity: str, registry: RegistryDep) -> list[dict]:
    skill = registry.entity_by_name.get(entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity!r} not found")
    mf = registry.mutations_by_entity.get(entity)
    if mf is None:
        return []
    return format_mutation_defs(mf)


# ---------------------------------------------------------------------------
# Resolve endpoint (NL input → intent classification)
# ---------------------------------------------------------------------------

# Synonyms for entity names that users commonly use in natural language.
# Maps lowercase synonym → registered entity name.
_ENTITY_SYNONYMS: dict[str, str] = {
    "song": "Track",
    "songs": "Track",
    "track": "Track",
    "tracks": "Track",
    "purchase": "Invoice",
    "purchases": "Invoice",
    "order": "Invoice",
    "orders": "Invoice",
    "sale": "Invoice",
    "sales": "Invoice",
    "invoice": "Invoice",
    "invoices": "Invoice",
    "line": "InvoiceLine",
    "lines": "InvoiceLine",
    "album": "Album",
    "albums": "Album",
    "artist": "Artist",
    "artists": "Artist",
    "customer": "Customer",
    "customers": "Customer",
    "employee": "Employee",
    "employees": "Employee",
    "genre": "Genre",
    "genres": "Genre",
    "media": "MediaType",
    "mediatype": "MediaType",
    "mediatypes": "MediaType",
    "playlist": "Playlist",
    "playlists": "Playlist",
}


def _extract_entity(query: str, known_entities: list[str]) -> str | None:
    """
    Scan the query for entity name keywords and return the entity whose
    keyword appears earliest.  Falls back to checking registered entity
    names directly (singular + plural) in case the synonym map is incomplete.
    """
    q_lower = query.lower()
    words = q_lower.split()

    best_pos: int = len(q_lower) + 1
    best_entity: str | None = None

    # Check synonym map
    for word in words:
        entity = _ENTITY_SYNONYMS.get(word)
        if entity and entity in known_entities:
            pos = q_lower.index(word)
            if pos < best_pos:
                best_pos = pos
                best_entity = entity

    # Check registered entity names directly (handles any entity not in map)
    for entity in known_entities:
        for candidate in (entity.lower(), entity.lower() + "s"):
            idx = q_lower.find(candidate)
            if idx != -1 and idx < best_pos:
                best_pos = idx
                best_entity = entity

    return best_entity


class ResolveRequest(BaseModel):
    input: str


class ResolveResponse(BaseModel):
    intent: str  # READ | MUTATE | VISUALIZE | NAVIGATE
    entity: str | None = None
    pattern_id: str | None = None
    confidence: float | None = None
    query_plan: dict | None = None
    did_you_mean: str | None = None
    source: Literal["pattern_cache", "llm_synthesis", "none"] = "none"


@router.post("/resolve", summary="Classify NL input + return QueryPlan")
async def resolve_input(body: ResolveRequest, request: Request) -> ResolveResponse:
    """
    Intent resolution pipeline:
    1. Rule engine (fast, 0 LLM calls)
    2. Pattern cache lookup (fuzzy match, 0 LLM calls if hit >= 0.90)
    3. LLM fallback for confidence < 0.80 (Phase 2)
    """
    from backend.pattern_cache.cache.pattern_cache import PatternCache

    raw_input = body.input
    if len(raw_input) > 500:
        raise HTTPException(status_code=400, detail="Input exceeds 500 character limit")

    # Hash input for safe logging (never log raw user input)
    input_hash = hashlib.sha256(raw_input.encode()).hexdigest()[:16]
    log.info("api.resolve.request", input_hash=input_hash, length=len(raw_input))

    pattern_cache: PatternCache = getattr(request.app.state, "pattern_cache", None)
    if pattern_cache is None:
        log.warning("api.resolve.no_pattern_cache")
        return ResolveResponse(intent="READ")

    result = pattern_cache.lookup(raw_input)
    if result is None or result.tier == "cache_miss":
        log.info("api.resolve.cache_miss", input_hash=input_hash)
        # Entity-name extraction fallback: navigate to the entity the user
        # mentioned even if we can't parse the full query semantics.
        registry: SkillRegistry = request.app.state.skill_registry
        known = list(registry.entity_by_name.keys())
        entity = _extract_entity(raw_input, known)
        if entity:
            log.info("api.resolve.entity_extracted", entity=entity, input_hash=input_hash)
            return ResolveResponse(intent="READ", entity=entity, confidence=0.5)

        # --- LLM synthesis fallback ---
        from backend.skill_registry.llm.query_synthesiser import QuerySynthesiser
        from backend.skill_registry.config.settings import llm_settings

        synthesiser: QuerySynthesiser = getattr(request.app.state, "query_synthesiser", None)
        if synthesiser is None:
            return ResolveResponse(intent="READ", confidence=0.0, source="none")

        query_plan_and_confidence = await synthesiser.synthesise(raw_input, registry)
        if query_plan_and_confidence is None:
            return ResolveResponse(intent="READ", confidence=0.0, source="none")
        query_plan, synthesis_confidence = query_plan_and_confidence

        skill = registry.entity_by_name.get(query_plan.entity)
        if skill is None:
            return ResolveResponse(intent="READ", confidence=0.0, source="none")

        from backend.adapters.registry import get_adapter_registry
        adapter_reg = get_adapter_registry()
        adapter = adapter_reg.get(skill.adapter)
        if adapter is None:
            return ResolveResponse(intent="READ", confidence=0.0, source="none")

        try:
            result = await adapter.execute_query(skill, query_plan)
        except Exception as exc:
            log.warning("api.resolve.execute_failed", error=str(exc), input_hash=input_hash)
            return ResolveResponse(intent="READ", confidence=0.0, source="none")

        # Async promotion — fire and forget, never blocks response
        promoter = getattr(request.app.state, "pattern_promoter", None)
        if promoter is not None:
            import asyncio
            asyncio.create_task(
                promoter.promote(
                    user_input=raw_input,
                    query_plan=query_plan,
                    confidence=synthesis_confidence,
                    entity=query_plan.entity,
                )
            )

        log.info("api.resolve.llm_synthesis", input_hash=input_hash, entity=query_plan.entity)
        return ResolveResponse(
            intent="READ",
            entity=query_plan.entity,
            confidence=synthesis_confidence,
            query_plan={"rows": result.rows, "total_count": result.total_count},
            source="llm_synthesis",
        )

    log.info(
        "api.resolve.cache_hit",
        input_hash=input_hash,
        pattern_id=result.pattern_id,
        confidence=result.confidence,
    )

    if result.confidence >= 0.90:
        return ResolveResponse(
            intent="READ",
            entity=result.entity,
            pattern_id=result.pattern_id,
            confidence=result.confidence,
            source="pattern_cache",
        )
    elif result.confidence >= 0.80:
        return ResolveResponse(
            intent="READ",
            entity=result.entity,
            pattern_id=result.pattern_id,
            confidence=result.confidence,
            did_you_mean=result.matched_trigger,
            source="pattern_cache",
        )
    else:
        # < 0.80 — cache miss, LLM fallback (Phase 2 stub)
        return ResolveResponse(intent="READ", confidence=result.confidence)


# ---------------------------------------------------------------------------
# Mutation preview/execute endpoints
# ---------------------------------------------------------------------------


class MutationPlanRequest(BaseModel):
    entity: str
    mutation_id: str
    record_pk: str | None = None
    fields: dict[str, Any] = {}


@router.post("/mutate/preview", summary="Generate diff preview — no DB write")
async def mutation_preview(body: MutationPlanRequest, request: Request) -> dict:
    """Build a diff preview in memory. Does NOT write to the database."""
    from backend.adapters.base import MutationPlan
    from backend.adapters.registry import get_adapter_registry

    registry: SkillRegistry = request.app.state.skill_registry
    skill = registry.entity_by_name.get(body.entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {body.entity!r} not found")

    mf = registry.mutations_by_entity.get(body.entity)
    if mf is None:
        raise HTTPException(status_code=400, detail=f"Entity {body.entity!r} has no mutations")

    mutation_def = next((m for m in mf.mutations if m.id == body.mutation_id), None)
    if mutation_def is None:
        raise HTTPException(
            status_code=404, detail=f"Mutation {body.mutation_id!r} not found"
        )

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Adapter {skill.adapter!r} not found")

    plan = MutationPlan(
        entity=body.entity,
        mutation_id=body.mutation_id,
        operation=mutation_def.operation,
        record_pk=body.record_pk,
        fields=body.fields,
    )
    diff = await adapter.preview_mutation(skill, plan)
    log.info("api.mutate.preview", entity=body.entity, mutation_id=body.mutation_id)
    return diff


@router.post("/mutate/execute", summary="Execute confirmed mutation in transaction")
async def mutation_execute(body: MutationPlanRequest, request: Request) -> dict:
    """Execute a confirmed mutation. Always runs within a DB transaction."""
    from backend.adapters.base import MutationPlan
    from backend.adapters.registry import get_adapter_registry

    registry: SkillRegistry = request.app.state.skill_registry
    skill = registry.entity_by_name.get(body.entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {body.entity!r} not found")

    mf = registry.mutations_by_entity.get(body.entity)
    if mf is None:
        raise HTTPException(status_code=400, detail=f"Entity {body.entity!r} has no mutations")

    mutation_def = next((m for m in mf.mutations if m.id == body.mutation_id), None)
    if mutation_def is None:
        raise HTTPException(
            status_code=404, detail=f"Mutation {body.mutation_id!r} not found"
        )

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Adapter {skill.adapter!r} not found")

    plan = MutationPlan(
        entity=body.entity,
        mutation_id=body.mutation_id,
        operation=mutation_def.operation,
        record_pk=body.record_pk,
        fields=body.fields,
    )
    result = await adapter.execute_mutation(skill, plan)
    log.info(
        "api.mutate.execute",
        entity=body.entity,
        mutation_id=body.mutation_id,
        success=result.get("success"),
    )
    return result
