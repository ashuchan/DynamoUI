"""REST routes for schedules + alerts."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.auth.api.dependencies import AuthContext, get_current_context
from backend.scheduling.models.dtos import (
    AlertCreate,
    AlertRead,
    AlertUpdate,
    DeliveryRunRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from backend.scheduling.services.alert_service import (
    AlertNotFound,
    AlertService,
)
from backend.scheduling.services.schedule_service import (
    ScheduleNotFound,
    ScheduleService,
)

router = APIRouter()


def _sched(request: Request) -> ScheduleService:
    svc: ScheduleService | None = getattr(request.app.state, "schedule_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scheduling subsystem unavailable",
        )
    return svc


def _alert(request: Request) -> AlertService:
    svc: AlertService | None = getattr(request.app.state, "alert_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="alerts subsystem unavailable",
        )
    return svc


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@router.get("/schedules", response_model=list[ScheduleRead])
async def list_schedules(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[ScheduleRead]:
    return await _sched(request).list(owner_id=ctx.user.id)


@router.post("/schedules", response_model=ScheduleRead, status_code=201)
async def create_schedule(
    payload: ScheduleCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> ScheduleRead:
    try:
        return await _sched(request).create(
            owner_id=ctx.user.id, tenant_id=ctx.tenant.id, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/schedules/{schedule_id}", response_model=ScheduleRead)
async def get_schedule(
    schedule_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> ScheduleRead:
    try:
        return await _sched(request).get(schedule_id, owner_id=ctx.user.id)
    except ScheduleNotFound:
        raise HTTPException(status_code=404, detail="schedule not found")


@router.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
async def update_schedule(
    schedule_id: UUID,
    payload: ScheduleUpdate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> ScheduleRead:
    try:
        return await _sched(request).update(
            schedule_id, owner_id=ctx.user.id, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _sched(request).delete(schedule_id, owner_id=ctx.user.id)


@router.post("/schedules/{schedule_id}/test", response_model=DeliveryRunRead)
async def test_schedule(
    schedule_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> DeliveryRunRead:
    try:
        return await _sched(request).test_fire(schedule_id, owner_id=ctx.user.id)
    except ScheduleNotFound:
        raise HTTPException(status_code=404, detail="schedule not found")


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(
    schedule_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = Query(None),
) -> dict:
    return await _sched(request).list_runs(
        schedule_id, owner_id=ctx.user.id, limit=limit, before=before
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts", response_model=list[AlertRead])
async def list_alerts(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[AlertRead]:
    return await _alert(request).list(owner_id=ctx.user.id)


@router.post("/alerts", response_model=AlertRead, status_code=201)
async def create_alert(
    payload: AlertCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> AlertRead:
    try:
        return await _alert(request).create(
            owner_id=ctx.user.id, tenant_id=ctx.tenant.id, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/alerts/{alert_id}", response_model=AlertRead)
async def get_alert(
    alert_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> AlertRead:
    try:
        return await _alert(request).get(alert_id, owner_id=ctx.user.id)
    except AlertNotFound:
        raise HTTPException(status_code=404, detail="alert not found")


@router.patch("/alerts/{alert_id}", response_model=AlertRead)
async def update_alert(
    alert_id: UUID,
    payload: AlertUpdate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> AlertRead:
    return await _alert(request).update(
        alert_id, owner_id=ctx.user.id, payload=payload
    )


@router.delete("/alerts/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _alert(request).delete(alert_id, owner_id=ctx.user.id)


@router.get("/alerts/{alert_id}/triggers")
async def list_alert_triggers(
    alert_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    # Trigger history lives in dui_delivery_run (alert_id column). We can
    # reuse the schedule-runs pagination but for alerts specifically.
    return {"triggers": [], "nextCursor": None}
