"""REST routes for saved views, dashboards, pins, home."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.auth.api.dependencies import AuthContext, get_current_context
from backend.personalisation.models.dtos import (
    DashboardCreate,
    DashboardRead,
    DashboardUpdate,
    PinCreate,
    PinRead,
    SavedViewCreate,
    SavedViewRead,
    SavedViewUpdate,
    TileCreate,
    TileRead,
    TileUpdate,
)
from backend.personalisation.services.dashboard_service import (
    DashboardNotFound,
    DashboardService,
    PinService,
)
from backend.personalisation.services.personalisation_service import PersonalisationService
from backend.personalisation.services.saved_view_service import (
    SavedViewNotFound,
    SavedViewService,
)
from backend.query_engine.provenance import ExecutedResult

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _get_facade(request: Request) -> PersonalisationService:
    svc: PersonalisationService | None = getattr(
        request.app.state, "personalisation_service", None
    )
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="personalisation subsystem unavailable",
        )
    return svc


def _sv(request: Request) -> SavedViewService:
    return _get_facade(request).saved_views


def _dash(request: Request) -> DashboardService:
    return _get_facade(request).dashboards


def _pins(request: Request) -> PinService:
    return _get_facade(request).pins


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


@router.get("/views", response_model=list[SavedViewRead])
async def list_views(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    entity: str | None = Query(None),
    shared: bool = Query(False),
) -> list[SavedViewRead]:
    return await _sv(request).list(owner_id=ctx.user.id, entity=entity, shared=shared)


@router.post("/views", response_model=SavedViewRead, status_code=201)
async def create_view(
    payload: SavedViewCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> SavedViewRead:
    return await _sv(request).create(
        owner_id=ctx.user.id, tenant_id=ctx.tenant.id, payload=payload
    )


@router.get("/views/{view_id}", response_model=SavedViewRead)
async def get_view(
    view_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> SavedViewRead:
    try:
        return await _sv(request).get(view_id, owner_id=ctx.user.id)
    except SavedViewNotFound:
        raise HTTPException(status_code=404, detail="saved view not found")


@router.patch("/views/{view_id}", response_model=SavedViewRead)
async def update_view(
    view_id: UUID,
    payload: SavedViewUpdate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> SavedViewRead:
    return await _sv(request).update(view_id, owner_id=ctx.user.id, payload=payload)


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(
    view_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _sv(request).delete(view_id, owner_id=ctx.user.id)


@router.post("/views/{view_id}/execute")
async def execute_view(
    view_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> ExecutedResult:
    try:
        return await _sv(request).execute(view_id, owner_id=ctx.user.id)
    except SavedViewNotFound:
        raise HTTPException(status_code=404, detail="saved view not found")


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


@router.get("/dashboards", response_model=list[DashboardRead])
async def list_dashboards(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[DashboardRead]:
    return await _dash(request).list(owner_id=ctx.user.id)


@router.post("/dashboards", response_model=DashboardRead, status_code=201)
async def create_dashboard(
    payload: DashboardCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> DashboardRead:
    return await _dash(request).create(
        owner_id=ctx.user.id, tenant_id=ctx.tenant.id, payload=payload
    )


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard_tree(
    dashboard_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    try:
        return await _dash(request).get_tree(dashboard_id, owner_id=ctx.user.id)
    except DashboardNotFound:
        raise HTTPException(status_code=404, detail="dashboard not found")


@router.patch("/dashboards/{dashboard_id}", response_model=DashboardRead)
async def update_dashboard(
    dashboard_id: UUID,
    payload: DashboardUpdate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> DashboardRead:
    try:
        return await _dash(request).update(
            dashboard_id, owner_id=ctx.user.id, payload=payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(
    dashboard_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _dash(request).delete(dashboard_id, owner_id=ctx.user.id)


@router.post("/dashboards/{dashboard_id}/tiles", response_model=TileRead, status_code=201)
async def add_tile(
    dashboard_id: UUID,
    payload: TileCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> TileRead:
    return await _dash(request).add_tile(
        dashboard_id, owner_id=ctx.user.id, payload=payload
    )


@router.patch("/dashboards/{dashboard_id}/tiles/{tile_id}", response_model=TileRead)
async def update_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    payload: TileUpdate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> TileRead:
    return await _dash(request).update_tile(
        dashboard_id, tile_id, owner_id=ctx.user.id, payload=payload
    )


@router.delete("/dashboards/{dashboard_id}/tiles/{tile_id}", status_code=204)
async def delete_tile(
    dashboard_id: UUID,
    tile_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _dash(request).delete_tile(dashboard_id, tile_id, owner_id=ctx.user.id)


# ---------------------------------------------------------------------------
# Pins + Home
# ---------------------------------------------------------------------------


@router.get("/pins", response_model=list[PinRead])
async def list_pins(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> list[PinRead]:
    return await _pins(request).list(user_id=ctx.user.id)


@router.post("/pins", response_model=PinRead, status_code=201)
async def create_pin(
    payload: PinCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> PinRead:
    return await _pins(request).create(
        user_id=ctx.user.id, tenant_id=ctx.tenant.id, payload=payload
    )


@router.delete("/pins/{pin_id}", status_code=204)
async def delete_pin(
    pin_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _pins(request).delete(pin_id, user_id=ctx.user.id)


@router.get("/home")
async def get_home(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return await _get_facade(request).compose_home(user_id=ctx.user.id)
