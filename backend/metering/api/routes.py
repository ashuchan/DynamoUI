"""
FastAPI router for the metering subsystem.
All routes are prefixed with /api/v1/metering by main.py.

Phase 1: read-only endpoints + POST /cost-rates for rate management.
Auth is added in Phase 2 alongside the JWT middleware.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.metering.dto.cost_rate_dto import CostRateCreateDTO, CostRateReadDTO
from backend.metering.dto.interaction_dto import LLMInteractionReadDTO
from backend.metering.dto.operation_dto import OperationReadDTO
from backend.metering.service import MeteringService

log = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_metering_service(request: Request) -> MeteringService:
    svc: MeteringService | None = getattr(request.app.state, "metering_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Metering service not available")
    return svc


MeteringSvcDep = Annotated[MeteringService, Depends(get_metering_service)]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@router.get("/summary", summary="Aggregated metering summary over a time range")
async def metering_summary(
    svc: MeteringSvcDep,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    operation_type: str | None = Query(None),
) -> dict:
    """
    Returns total operations, cache hit rate, total tokens, and total cost.
    Optionally filter by time range and operation_type.
    """
    cost_rows = await svc.cost_by_model(from_ts=from_ts, to_ts=to_ts)
    total_cost = sum(float(r.get("total_cost_usd") or 0) for r in cost_rows)
    total_tokens = sum(int(r.get("total_tokens") or 0) for r in cost_rows)
    total_interactions = sum(int(r.get("interaction_count") or 0) for r in cost_rows)

    # Cheap operation count (no filter by time in Phase 1)
    ops = await svc.list_operations(operation_type=operation_type, page=1, page_size=1000)
    total_ops = len(ops)
    cache_hits = sum(1 for o in ops if o.cache_hit is True)
    cache_hit_rate = round(cache_hits / total_ops, 4) if total_ops else 0.0

    return {
        "total_operations": total_ops,
        "cache_hit_rate": cache_hit_rate,
        "total_llm_interactions": total_interactions,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
    }


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


@router.get("/operations", summary="Paginated list of metering operations")
async def list_operations(
    svc: MeteringSvcDep,
    operation_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> list[OperationReadDTO]:
    return await svc.list_operations(
        operation_type=operation_type, page=page, page_size=page_size
    )


@router.get(
    "/operations/{operation_id}",
    summary="Single operation with its LLM interactions",
)
async def get_operation(
    operation_id: UUID,
    svc: MeteringSvcDep,
) -> dict:
    op = await svc.get_operation(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    interactions = await svc.get_interactions_for_operation(operation_id)
    return {
        "operation": op.model_dump(),
        "interactions": [i.model_dump() for i in interactions],
    }


# ---------------------------------------------------------------------------
# Cost breakdown
# ---------------------------------------------------------------------------


@router.get("/cost-by-model", summary="Aggregated cost breakdown by provider+model")
async def cost_by_model(
    svc: MeteringSvcDep,
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
) -> list[dict]:
    return await svc.cost_by_model(from_ts=from_ts, to_ts=to_ts)


# ---------------------------------------------------------------------------
# Cost rates — append-only ledger
# ---------------------------------------------------------------------------


@router.get("/cost-rates", summary="Full history of LLM cost rates")
async def list_cost_rates(
    svc: MeteringSvcDep,
    provider: str | None = Query(None),
    model: str | None = Query(None),
) -> list[CostRateReadDTO]:
    return await svc.list_cost_rates(provider=provider, model=model)


@router.post(
    "/cost-rates",
    summary="Add a new cost rate version (supersedes the current active rate)",
    status_code=201,
)
async def add_cost_rate(
    body: CostRateCreateDTO,
    svc: MeteringSvcDep,
) -> CostRateReadDTO:
    """
    Supersede the active rate for (provider, model) with a new one.
    Both change_reason and created_by are required.
    All previous interactions retain their original cost_rate_id FK — the
    historical cost record is preserved.
    """
    try:
        rate = await svc.add_cost_rate(body)
    except Exception as exc:
        log.warning("api.metering.add_cost_rate_failed", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    log.info(
        "api.metering.cost_rate_added",
        provider=body.provider,
        model=body.model,
        effective_from=str(body.effective_from),
        created_by=body.created_by,
    )
    return rate
