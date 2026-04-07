"""REST routes for ``/api/v1/admin/registry``."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from backend.auth.api.dependencies import AuthContext, require_role
from backend.tenants.registry.dao import UnknownResourceTypeError
from backend.tenants.registry.dtos import (
    RegistryEntryRead,
    RegistryEntrySummary,
    RegistryUpsertRequest,
    ResourceType,
)
from backend.tenants.registry.service import (
    InvalidYAMLError,
    RegistryEntryNotFoundError,
    RegistryService,
)

router = APIRouter(prefix="/admin/registry")


def _get_service(request: Request) -> RegistryService:
    svc: RegistryService | None = getattr(request.app.state, "registry_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="registry subsystem unavailable",
        )
    return svc


_admin_dep = require_role("owner", "admin")
_member_dep = require_role("owner", "admin", "member")  # read-only access


@router.get("/types", response_model=list[str])
async def list_types(
    _: Annotated[AuthContext, Depends(_member_dep)],
) -> list[str]:
    return RegistryService.supported_types()


@router.get("/{resource_type}", response_model=list[RegistryEntrySummary])
async def list_entries(
    resource_type: Annotated[ResourceType, Path()],
    ctx: Annotated[AuthContext, Depends(_member_dep)],
    svc: Annotated[RegistryService, Depends(_get_service)],
) -> list[RegistryEntrySummary]:
    try:
        return await svc.list(ctx.tenant.id, resource_type)
    except UnknownResourceTypeError:
        raise HTTPException(status_code=400, detail="unknown resource type")


@router.get("/{resource_type}/{name}", response_model=RegistryEntryRead)
async def get_entry(
    resource_type: Annotated[ResourceType, Path()],
    name: str,
    ctx: Annotated[AuthContext, Depends(_member_dep)],
    svc: Annotated[RegistryService, Depends(_get_service)],
) -> RegistryEntryRead:
    try:
        return await svc.get(ctx.tenant.id, resource_type, name)
    except RegistryEntryNotFoundError:
        raise HTTPException(status_code=404, detail="entry not found")
    except UnknownResourceTypeError:
        raise HTTPException(status_code=400, detail="unknown resource type")


@router.put("/{resource_type}/{name}", response_model=RegistryEntryRead)
async def upsert_entry(
    resource_type: Annotated[ResourceType, Path()],
    name: str,
    payload: RegistryUpsertRequest,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[RegistryService, Depends(_get_service)],
) -> RegistryEntryRead:
    if payload.name != name:
        raise HTTPException(status_code=400, detail="name mismatch between path and body")
    try:
        return await svc.upsert(
            tenant_id=ctx.tenant.id,
            resource_type=resource_type,
            name=name,
            yaml_source=payload.yaml_source,
        )
    except InvalidYAMLError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except UnknownResourceTypeError:
        raise HTTPException(status_code=400, detail="unknown resource type")


@router.delete("/{resource_type}/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    resource_type: Annotated[ResourceType, Path()],
    name: str,
    ctx: Annotated[AuthContext, Depends(_admin_dep)],
    svc: Annotated[RegistryService, Depends(_get_service)],
) -> None:
    try:
        await svc.delete(ctx.tenant.id, resource_type, name)
    except RegistryEntryNotFoundError:
        raise HTTPException(status_code=404, detail="entry not found")
    except UnknownResourceTypeError:
        raise HTTPException(status_code=400, detail="unknown resource type")
