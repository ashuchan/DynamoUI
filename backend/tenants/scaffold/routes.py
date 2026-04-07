"""REST routes for ``/api/v1/admin/scaffold-jobs`` and the
``/admin/connections/{id}/scaffold`` trigger.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from backend.auth.api.dependencies import AuthContext, require_role
from backend.tenants.connections.service import ConnectionNotFoundError
from backend.tenants.scaffold.dtos import ScaffoldJobRead, ScaffoldStartRequest
from backend.tenants.scaffold.service import (
    ScaffoldNotFoundError,
    ScaffoldService,
)

router = APIRouter(prefix="/admin")


def _get_service(request: Request) -> ScaffoldService:
    svc: ScaffoldService | None = getattr(request.app.state, "scaffold_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scaffold subsystem unavailable",
        )
    return svc


_admin_dep = require_role("owner", "admin")


@router.post(
    "/connections/{connection_id}/scaffold",
    response_model=ScaffoldJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_scaffold(
    connection_id: UUID,
    payload: ScaffoldStartRequest,
    background_tasks: BackgroundTasks,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ScaffoldService, Depends(_get_service)],
) -> ScaffoldJobRead:
    try:
        job = await svc.start(
            tenant_id=ctx.tenant.id,
            connection_id=connection_id,
            request=payload,
        )
    except ConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="connection not found")
    background_tasks.add_task(
        svc.run,
        tenant_id=ctx.tenant.id,
        job_id=job.id,
        connection_id=connection_id,
        request=payload,
    )
    return job


@router.get("/scaffold-jobs", response_model=list[ScaffoldJobRead])
async def list_scaffold_jobs(
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ScaffoldService, Depends(_get_service)],
) -> list[ScaffoldJobRead]:
    return await svc.list(ctx.tenant.id)


@router.get("/scaffold-jobs/{job_id}", response_model=ScaffoldJobRead)
async def get_scaffold_job(
    job_id: UUID,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ScaffoldService, Depends(_get_service)],
) -> ScaffoldJobRead:
    try:
        return await svc.get(ctx.tenant.id, job_id)
    except ScaffoldNotFoundError:
        raise HTTPException(status_code=404, detail="scaffold job not found")
