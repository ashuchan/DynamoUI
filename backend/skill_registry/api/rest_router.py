"""
FastAPI router for the Skill Registry module.
Covers: /enums, /schema, /widgets, /mutate endpoints.
All routes are prefixed with /api/v1 by the main app.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Any

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


class ResolveRequest(BaseModel):
    input: str


class ResolveResponse(BaseModel):
    intent: str  # READ | MUTATE | VISUALIZE | NAVIGATE
    entity: str | None = None
    pattern_id: str | None = None
    confidence: float | None = None
    query_plan: dict | None = None
    did_you_mean: str | None = None


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
    if result is None:
        log.info("api.resolve.cache_miss", input_hash=input_hash)
        # Phase 2: LLM fallback goes here
        return ResolveResponse(intent="READ", confidence=0.0)

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
        )
    elif result.confidence >= 0.80:
        return ResolveResponse(
            intent="READ",
            entity=result.entity,
            pattern_id=result.pattern_id,
            confidence=result.confidence,
            did_you_mean=result.matched_trigger,
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
