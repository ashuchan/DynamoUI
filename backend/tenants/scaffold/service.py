"""Scaffold orchestration service.

A scaffold job inspects a tenant's database connection and produces draft
skill / pattern / widget configs that are persisted into the tenant's
internal registry (Phase 4). Per-adapter inspection is delegated to a
``Scaffolder`` registered against the ``adapter_kind``.

In Phase 3 the orchestration is in place but the only registered scaffolder
is a no-op stub — Phase 5 plugs real adapter scaffolders in.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

import structlog

from backend.tenants.connections.dao import ConnectionRow
from backend.tenants.connections.service import ConnectionService
from backend.tenants.scaffold.dao import ScaffoldJobDAO, ScaffoldJobRow
from backend.tenants.scaffold.dtos import ScaffoldJobRead, ScaffoldStartRequest

log = structlog.get_logger(__name__)


class ScaffoldNotFoundError(Exception):
    pass


class Scaffolder(Protocol):
    """Adapter-specific scaffolder.

    Implementations receive the *materialised* (decrypted) connection dict
    and a ``progress`` callback they can call to update the job. They must
    return a ``result_summary`` dict that becomes the job payload.
    """

    async def scaffold(
        self,
        *,
        connection: dict[str, Any],
        request: ScaffoldStartRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> dict[str, Any]: ...


# A trivial default that just records the request shape so the orchestration
# can be exercised end-to-end without a live database.
class _NoopScaffolder:
    async def scaffold(
        self,
        *,
        connection: dict[str, Any],
        request: ScaffoldStartRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> dict[str, Any]:
        await progress(50)
        await progress(100)
        return {
            "adapter_kind": connection["adapter_kind"],
            "schema_filter": request.schema_filter,
            "tables_inspected": [],
            "skills_generated": 0,
            "warnings": [
                "noop scaffolder — register a real Scaffolder for this adapter_kind",
            ],
        }


class ScaffoldService:
    def __init__(
        self,
        *,
        dao: ScaffoldJobDAO,
        connection_service: ConnectionService,
    ) -> None:
        self._dao = dao
        self._connections = connection_service
        self._scaffolders: dict[str, Scaffolder] = {}
        self._noop = _NoopScaffolder()

    def register_scaffolder(self, adapter_kind: str, scaffolder: Scaffolder) -> None:
        self._scaffolders[adapter_kind] = scaffolder

    async def list(self, tenant_id: UUID) -> list[ScaffoldJobRead]:
        rows = await self._dao.list_for_tenant(tenant_id)
        return [_to_read(r) for r in rows]

    async def get(self, tenant_id: UUID, job_id: UUID) -> ScaffoldJobRead:
        row = await self._dao.get(tenant_id, job_id)
        if row is None:
            raise ScaffoldNotFoundError(str(job_id))
        return _to_read(row)

    async def start(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        request: ScaffoldStartRequest,
    ) -> ScaffoldJobRead:
        # Will raise ConnectionNotFoundError on cross-tenant attempts.
        await self._connections.get(tenant_id, connection_id)
        row = await self._dao.create(tenant_id=tenant_id, connection_id=connection_id)
        return _to_read(row)

    async def run(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID,
        connection_id: UUID,
        request: ScaffoldStartRequest,
    ) -> None:
        """Execute a previously-created job. Intended to be invoked from a
        ``BackgroundTasks`` callback or a worker."""
        try:
            await self._dao.update_progress(
                tenant_id=tenant_id, job_id=job_id, status="running", progress=1
            )
            # Materialise connection inside the async task so the decrypted
            # secret never crosses an await boundary in the request handler.
            row = await self._connections._dao.get(tenant_id, connection_id)  # noqa: SLF001
            if row is None:
                raise ScaffoldNotFoundError("connection vanished mid-job")
            materialised = self._connections.materialise(row)
            scaffolder = self._scaffolders.get(materialised["adapter_kind"], self._noop)

            async def progress(p: int) -> None:
                await self._dao.update_progress(
                    tenant_id=tenant_id, job_id=job_id, status="running", progress=p
                )

            summary = await scaffolder.scaffold(
                connection=materialised, request=request, progress=progress
            )
            await self._dao.update_progress(
                tenant_id=tenant_id,
                job_id=job_id,
                status="completed",
                progress=100,
                result_summary=summary,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("scaffold.failed", job_id=str(job_id), error=str(exc))
            await self._dao.update_progress(
                tenant_id=tenant_id,
                job_id=job_id,
                status="failed",
                progress=0,
                error=str(exc),
            )


def _to_read(row: ScaffoldJobRow) -> ScaffoldJobRead:
    return ScaffoldJobRead(
        id=row.id,
        tenant_id=row.tenant_id,
        connection_id=row.connection_id,
        status=row.status,
        progress=row.progress,
        result_summary=row.result_summary,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
