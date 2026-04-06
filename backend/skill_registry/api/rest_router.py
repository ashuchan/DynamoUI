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


def _parse_pattern_template_to_plan(
    query_template: str,
    entity: str,
) -> "Any | None":
    """
    Convert a pattern's query_template JSON string to a QueryPlan for execution.
    Handles both the QuerySynthesiser format and the LLM-seeder format.
    Returns None if the template cannot be parsed into an executable plan.
    """
    import json
    from backend.adapters.base import (
        QueryPlan, FilterClause, SortClause, JoinClause, AggregationClause
    )

    _OP_MAP = {
        "equals": "eq", "eq": "eq", "=": "eq",
        "not_equals": "ne", "ne": "ne",
        "gt": "gt", "greater_than": "gt",
        "gte": "gte", "greater_than_or_equal": "gte",
        "lt": "lt", "less_than": "lt",
        "lte": "lte", "less_than_or_equal": "lte",
        "like": "like", "ilike": "like", "contains": "like",
        "in": "in", "is_null": "is_null",
    }
    _FUNC_MAP = {
        "count": "count", "sum": "sum", "avg": "avg", "min": "min", "max": "max",
        "COUNT": "count", "SUM": "sum", "AVG": "avg", "MIN": "min", "MAX": "max",
    }

    try:
        data = json.loads(query_template)
    except (json.JSONDecodeError, TypeError):
        return None

    filters = []
    for f in data.get("filters", []):
        raw_op = f.get("op") or f.get("operator", "eq")
        op = _OP_MAP.get(str(raw_op).lower(), "eq")
        val = f.get("value", "")
        # Skip unfilled param placeholders like "{artist_id}" or "{{name}}"
        if isinstance(val, str) and val.startswith("{") and val.endswith("}"):
            continue
        try:
            filters.append(FilterClause(field=str(f["field"]), op=op, value=val))
        except (KeyError, Exception):
            continue

    sort = []
    for s in data.get("sort", []):
        direction = str(s.get("direction") or s.get("dir") or "asc").lower()
        if direction not in ("asc", "desc"):
            direction = "asc"
        try:
            sort.append(SortClause(field=str(s["field"]), dir=direction))
        except (KeyError, Exception):
            continue

    joins = []
    for j in data.get("joins", []):
        join_type = str(j.get("join_type") or j.get("type") or "inner").lower()
        if join_type not in ("inner", "left"):
            join_type = "inner"
        if "source_field" in j and "target_entity" in j and "target_field" in j:
            joins.append(JoinClause(
                source_field=j["source_field"], target_entity=j["target_entity"],
                target_field=j["target_field"], join_type=join_type,
            ))
        elif "left_key" in j and "right_key" in j and "entity" in j:
            joins.append(JoinClause(
                source_field=j["left_key"], target_entity=j["entity"],
                target_field=j["right_key"], join_type=join_type,
            ))
        elif "on" in j and "entity" in j:
            on_clause = str(j["on"])
            parts = [p.strip() for p in on_clause.split("=", 1)]
            if len(parts) == 2 and "." in parts[0] and "." in parts[1]:
                source_field = parts[0].split(".", 1)[1]
                target_field = parts[1].split(".", 1)[1]
                joins.append(JoinClause(
                    source_field=source_field, target_entity=j["entity"],
                    target_field=target_field, join_type=join_type,
                ))

    aggregations = []
    for a in data.get("aggregations", []):
        raw_func = a.get("func") or a.get("function", "count")
        func = _FUNC_MAP.get(str(raw_func), "count")
        try:
            aggregations.append(AggregationClause(
                func=func, field=str(a["field"]), alias=str(a["alias"])
            ))
        except (KeyError, Exception):
            continue

    result_limit = data.get("result_limit")
    if isinstance(result_limit, str):
        try:
            result_limit = int(result_limit)
        except ValueError:
            result_limit = None

    return QueryPlan(
        entity=entity,
        filters=filters,
        sort=sort,
        joins=joins,
        aggregations=aggregations,
        group_by=[str(g) for g in data.get("group_by", [])],
        result_limit=result_limit,
        page=1,
        page_size=result_limit or 25,
    )


async def _execute_pattern(
    pattern_id: str,
    entity: str,
    pattern_cache: Any,
    registry: "SkillRegistry",
    input_hash: str,
) -> "dict | None":
    """
    Fetch a pattern's query_template, parse it to a QueryPlan, and execute it.
    Returns {rows, total_count} or None if anything fails.
    """
    from backend.adapters.registry import get_adapter_registry

    pattern = pattern_cache.get_pattern(pattern_id)
    if pattern is None:
        log.warning("api.resolve.pattern_not_found", pattern_id=pattern_id,
                    input_hash=input_hash)
        return None

    query_template = (
        pattern.query_template
        if hasattr(pattern, "query_template")
        else (pattern.get("query_template", "{}") if isinstance(pattern, dict) else "{}")
    )

    plan = _parse_pattern_template_to_plan(query_template, entity)
    if plan is None:
        log.warning("api.resolve.template_parse_failed", pattern_id=pattern_id,
                    input_hash=input_hash)
        return None

    skill = registry.entity_by_name.get(entity)
    if skill is None:
        return None

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        return None

    try:
        exec_result = await adapter.execute_query(skill, plan)
        return {"rows": exec_result.rows, "total_count": exec_result.total_count}
    except Exception as exc:
        log.warning("api.resolve.pattern_execute_failed", pattern_id=pattern_id,
                    error=str(exc), input_hash=input_hash)
        return None


@router.post("/resolve", summary="Classify NL input + return QueryPlan")
async def resolve_input(body: ResolveRequest, request: Request) -> ResolveResponse:
    """
    Intent resolution pipeline:
    1. Pattern cache lookup — on hit (≥ 0.90) execute the pattern's query_template
    2. LLM synthesis — on cache miss, synthesise a QueryPlan and execute it
    On failure: returns confidence=0.0, source="none". No silent fallbacks.
    """
    import time as _time
    from backend.pattern_cache.cache.pattern_cache import PatternCache

    raw_input = body.input
    if len(raw_input) > 500:
        raise HTTPException(status_code=400, detail="Input exceeds 500 character limit")

    input_hash = hashlib.sha256(raw_input.encode()).hexdigest()[:16]
    input_hash_full = hashlib.sha256(raw_input.encode()).hexdigest()
    log.info("api.resolve.request", input_hash=input_hash, length=len(raw_input))

    registry: SkillRegistry = request.app.state.skill_registry
    pattern_cache: PatternCache = getattr(request.app.state, "pattern_cache", None)

    if pattern_cache is None:
        log.warning("api.resolve.no_pattern_cache")
        return ResolveResponse(intent="READ")

    # ── Metering: open an operation row ────────────────────────────────────────
    from backend.metering.context import MeteringContext, clear_metering_context, set_metering_context
    from backend.metering.dto.operation_dto import OperationUpdateDTO

    metering_svc = getattr(request.app.state, "metering_service", None)
    t0 = _time.monotonic()
    operation_id = None
    metering_ctx_token = None

    if metering_svc is not None:
        client_ip = (
            request.client.host if request.client else None
        )
        operation_id = await metering_svc.start_operation(
            operation_type="resolve",
            user_input_hash=input_hash_full,
            ip_address=client_ip,
        )
        metering_ctx_token = set_metering_context(
            MeteringContext(
                operation_id=operation_id,
                interaction_type="query_synthesis",
            )
        )

    response: ResolveResponse | None = None
    success = True
    error_msg: str | None = None

    try:
        cache_result = pattern_cache.lookup(raw_input)

        # ── 1. Pattern cache hit (≥ 0.90) — execute and return rows ─────────
        if (
            cache_result is not None
            and cache_result.confidence is not None
            and cache_result.confidence >= 0.90
        ):
            log.info("api.resolve.cache_hit",
                     input_hash=input_hash,
                     pattern_id=cache_result.pattern_id,
                     confidence=cache_result.confidence)

            query_data = await _execute_pattern(
                cache_result.pattern_id, cache_result.entity,
                pattern_cache, registry, input_hash,
            )
            response = ResolveResponse(
                intent="READ",
                entity=cache_result.entity,
                pattern_id=cache_result.pattern_id,
                confidence=cache_result.confidence,
                query_plan=query_data,
                did_you_mean=(
                    cache_result.matched_trigger
                    if cache_result.confidence < 0.95 else None
                ),
                source="pattern_cache",
            )
            return response

        # ── 2. LLM synthesis — handles complex cross-entity queries ──────────
        synthesiser = getattr(request.app.state, "query_synthesiser", None)
        if synthesiser is not None:
            query_plan_and_confidence = await synthesiser.synthesise(raw_input, registry)
            if query_plan_and_confidence is not None:
                query_plan, synthesis_confidence = query_plan_and_confidence
                skill = registry.entity_by_name.get(query_plan.entity)
                if skill is not None:
                    from backend.adapters.registry import get_adapter_registry
                    adapter_reg = get_adapter_registry()
                    adapter = adapter_reg.get(skill.adapter)
                    if adapter is not None:
                        try:
                            exec_result = await adapter.execute_query(skill, query_plan)

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

                            log.info("api.resolve.llm_synthesis",
                                     input_hash=input_hash, entity=query_plan.entity,
                                     confidence=synthesis_confidence)
                            response = ResolveResponse(
                                intent="READ",
                                entity=query_plan.entity,
                                confidence=synthesis_confidence,
                                query_plan={
                                    "rows": exec_result.rows,
                                    "total_count": exec_result.total_count,
                                },
                                source="llm_synthesis",
                            )
                            return response
                        except Exception as exc:
                            log.warning("api.resolve.llm_execute_failed",
                                        error=str(exc), input_hash=input_hash)
                            error_msg = str(exc)[:1000]
                            success = False

        log.info("api.resolve.unresolved", input_hash=input_hash)
        response = ResolveResponse(intent="READ", confidence=0.0, source="none")
        return response

    except Exception:
        success = False
        raise

    finally:
        # ── Metering: close the operation row ─────────────────────────────────
        if metering_ctx_token is not None:
            clear_metering_context(metering_ctx_token)
        if metering_svc is not None and operation_id is not None:
            rows = None
            entity = None
            pattern_id = None
            cache_hit = None
            confidence = None
            if response is not None:
                rows = (
                    response.query_plan.get("total_count")
                    if isinstance(response.query_plan, dict) else None
                )
                entity = response.entity
                pattern_id = response.pattern_id
                cache_hit = response.source == "pattern_cache"
                confidence = response.confidence
            await metering_svc.complete_operation(
                operation_id,
                OperationUpdateDTO(
                    entity=entity,
                    pattern_id=pattern_id,
                    cache_hit=cache_hit,
                    confidence=confidence,
                    success=success,
                    error_message=error_msg,
                    rows_returned=rows,
                    duration_ms=int((_time.monotonic() - t0) * 1000),
                ),
            )


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
