"""REST routes for ``/api/v1/admin/connections``.

All routes require an authenticated owner or admin via
:func:`backend.auth.api.dependencies.require_role`.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from backend.auth.api.dependencies import AuthContext, require_role
from backend.crypto.envelope import CryptoError, CryptoNotConfiguredError
from backend.tenants.connections.dtos import (
    ConnectionCreate,
    ConnectionRead,
    ConnectionTestResult,
    ConnectionUpdate,
)
from backend.tenants.connections.service import (
    ConnectionNotFoundError,
    ConnectionService,
    DuplicateConnectionError,
)

router = APIRouter(prefix="/admin/connections")


def _get_service(request: Request) -> ConnectionService:
    svc: ConnectionService | None = getattr(
        request.app.state, "connection_service", None
    )
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="connections subsystem unavailable",
        )
    return svc


_admin_dep = require_role("owner", "admin")


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> list[ConnectionRead]:
    return await svc.list(ctx.tenant.id)


@router.post("", response_model=ConnectionRead, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreate,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> ConnectionRead:
    try:
        return await svc.create(ctx.tenant.id, payload)
    except DuplicateConnectionError as exc:
        raise HTTPException(status_code=409, detail=f"name already in use: {exc}")
    except CryptoNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except CryptoError as exc:
        raise HTTPException(status_code=500, detail=f"crypto error: {exc}")


@router.get("/{connection_id}", response_model=ConnectionRead)
async def get_connection(
    connection_id: UUID,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> ConnectionRead:
    try:
        return await svc.get(ctx.tenant.id, connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="connection not found")


@router.patch("/{connection_id}", response_model=ConnectionRead)
async def update_connection(
    connection_id: UUID,
    payload: ConnectionUpdate,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> ConnectionRead:
    try:
        return await svc.update(ctx.tenant.id, connection_id, payload)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="connection not found")
    except DuplicateConnectionError as exc:
        raise HTTPException(status_code=409, detail=f"name already in use: {exc}")


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> None:
    try:
        await svc.delete(ctx.tenant.id, connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="connection not found")


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: UUID,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[ConnectionService, Depends(_get_service)],
) -> ConnectionTestResult:
    try:
        return await svc.test(ctx.tenant.id, connection_id)
    except ConnectionNotFoundError:
        raise HTTPException(status_code=404, detail="connection not found")
