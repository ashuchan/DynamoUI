"""
Entities API — /api/v1/entities/{entity} endpoints.
Fetches data via the registered DataAdapter for each entity.
"""
from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.adapters.base import FilterClause, QueryPlan, SortClause
from backend.adapters.registry import get_adapter_registry
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)

router = APIRouter()


def get_registry(request: Request) -> SkillRegistry:
    return request.app.state.skill_registry


RegistryDep = Annotated[SkillRegistry, Depends(get_registry)]


@router.get("/entities/{entity}", summary="Fetch entity list (sort/filter/page)")
async def fetch_entity_list(
    entity: str,
    request: Request,
    registry: RegistryDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    sort_field: str | None = Query(None),
    sort_dir: str = Query("asc"),
    filter_field: list[str] | None = Query(None),
    filter_op: list[str] | None = Query(None),
    filter_value: list[str] | None = Query(None),
) -> dict[str, Any]:
    skill = registry.entity_by_name.get(entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity!r} not found")

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Adapter {skill.adapter!r} not available")

    # Build sort
    sort: list[SortClause] = []
    if sort_field:
        if sort_dir not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_dir must be 'asc' or 'desc'")
        sort.append(SortClause(field=sort_field, dir=sort_dir))
    elif skill.display.default_sort_field:
        sort.append(
            SortClause(
                field=skill.display.default_sort_field,
                dir=skill.display.default_sort_dir,
            )
        )

    # Build filters
    filters: list[FilterClause] = []
    if filter_field and filter_op and filter_value:
        if not (len(filter_field) == len(filter_op) == len(filter_value)):
            raise HTTPException(
                status_code=400,
                detail="filter_field, filter_op, and filter_value must have equal lengths",
            )
        for ff, fo, fv in zip(filter_field, filter_op, filter_value):
            filters.append(FilterClause(field=ff, op=fo, value=fv))

    plan = QueryPlan(
        entity=entity,
        filters=filters,
        sort=sort,
        page=page,
        page_size=page_size,
    )

    result = await adapter.execute_query(skill, plan)
    log.info(
        "api.entities.list",
        entity=entity,
        rows=len(result.rows),
        total=result.total_count,
        page=page,
    )

    return {
        "rows": result.rows,
        "totalCount": result.total_count,
        "page": result.page,
        "pageSize": result.page_size,
    }


@router.get("/entities/{entity}/{pk}", summary="Fetch single record by PK")
async def fetch_single_record(
    entity: str,
    pk: str,
    registry: RegistryDep,
) -> dict[str, Any]:
    skill = registry.entity_by_name.get(entity)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity!r} not found")

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Adapter {skill.adapter!r} not available")

    record = await adapter.fetch_single(skill, pk)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"{entity} record with PK {pk!r} not found"
        )

    log.debug("api.entities.single", entity=entity, pk=pk)
    return record
