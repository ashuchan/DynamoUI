"""DAO for ``tenant_scaffold_jobs``. All methods take ``tenant_id`` explicitly."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.tenants.scaffold.tables import tenant_scaffold_jobs


@dataclass(frozen=True)
class ScaffoldJobRow:
    id: UUID
    tenant_id: UUID
    connection_id: UUID
    status: str
    progress: int
    result_summary: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class ScaffoldJobDAO:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self, *, tenant_id: UUID, connection_id: UUID
    ) -> ScaffoldJobRow:
        new_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(tenant_scaffold_jobs).values(
                    id=new_id,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                )
            )
        row = await self.get(tenant_id, new_id)
        assert row is not None
        return row

    async def get(
        self, tenant_id: UUID, job_id: UUID
    ) -> ScaffoldJobRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(tenant_scaffold_jobs).where(
                        tenant_scaffold_jobs.c.tenant_id == tenant_id,
                        tenant_scaffold_jobs.c.id == job_id,
                    )
                )
            ).mappings().first()
        return _row(row) if row else None

    async def list_for_tenant(self, tenant_id: UUID) -> list[ScaffoldJobRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(tenant_scaffold_jobs)
                    .where(tenant_scaffold_jobs.c.tenant_id == tenant_id)
                    .order_by(tenant_scaffold_jobs.c.created_at.desc())
                )
            ).mappings().all()
        return [_row(r) for r in rows]

    async def update_progress(
        self,
        *,
        tenant_id: UUID,
        job_id: UUID,
        status: str,
        progress: int,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(tenant_scaffold_jobs)
                .where(
                    tenant_scaffold_jobs.c.tenant_id == tenant_id,
                    tenant_scaffold_jobs.c.id == job_id,
                )
                .values(
                    status=status,
                    progress=progress,
                    result_summary=result_summary,
                    error=error,
                    updated_at=sa.func.now(),
                )
            )


def _row(row: sa.engine.RowMapping) -> ScaffoldJobRow:
    return ScaffoldJobRow(
        id=row["id"],
        tenant_id=row["tenant_id"],
        connection_id=row["connection_id"],
        status=row["status"],
        progress=row["progress"],
        result_summary=row["result_summary"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
