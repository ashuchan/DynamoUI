"""DAO for ``tenant_db_connections``.

Every method takes ``tenant_id`` as an explicit argument and applies it as a
WHERE clause. Cross-tenant reads are impossible by construction — there is
no method on this class that accepts an id without a tenant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.tenants.connections.tables import tenant_db_connections


@dataclass(frozen=True)
class ConnectionRow:
    id: UUID
    tenant_id: UUID
    name: str
    adapter_kind: str
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    encrypted_secret: str | None
    options: dict[str, Any]
    status: str
    last_tested_at: datetime | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionDAO:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_for_tenant(self, tenant_id: UUID) -> list[ConnectionRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(tenant_db_connections)
                    .where(tenant_db_connections.c.tenant_id == tenant_id)
                    .order_by(tenant_db_connections.c.created_at.asc())
                )
            ).mappings().all()
        return [_row(r) for r in rows]

    async def get(
        self, tenant_id: UUID, connection_id: UUID
    ) -> ConnectionRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(tenant_db_connections).where(
                        tenant_db_connections.c.tenant_id == tenant_id,
                        tenant_db_connections.c.id == connection_id,
                    )
                )
            ).mappings().first()
        return _row(row) if row else None

    async def insert(
        self,
        *,
        tenant_id: UUID,
        name: str,
        adapter_kind: str,
        host: str | None,
        port: int | None,
        database: str | None,
        username: str | None,
        encrypted_secret: str | None,
        options: dict[str, Any],
    ) -> ConnectionRow:
        new_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(tenant_db_connections).values(
                    id=new_id,
                    tenant_id=tenant_id,
                    name=name,
                    adapter_kind=adapter_kind,
                    host=host,
                    port=port,
                    database=database,
                    username=username,
                    encrypted_secret=encrypted_secret,
                    options_json=options,
                )
            )
        row = await self.get(tenant_id, new_id)
        assert row is not None  # just inserted
        return row

    async def update(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        values: dict[str, Any],
    ) -> ConnectionRow | None:
        if not values:
            return await self.get(tenant_id, connection_id)
        values = {**values, "updated_at": sa.func.now()}
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(tenant_db_connections)
                .where(
                    tenant_db_connections.c.tenant_id == tenant_id,
                    tenant_db_connections.c.id == connection_id,
                )
                .values(**values)
            )
            if result.rowcount == 0:
                return None
        return await self.get(tenant_id, connection_id)

    async def delete(self, tenant_id: UUID, connection_id: UUID) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(tenant_db_connections).where(
                    tenant_db_connections.c.tenant_id == tenant_id,
                    tenant_db_connections.c.id == connection_id,
                )
            )
        return result.rowcount > 0

    async def record_test_result(
        self,
        *,
        tenant_id: UUID,
        connection_id: UUID,
        ok: bool,
        error: str | None,
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(tenant_db_connections)
                .where(
                    tenant_db_connections.c.tenant_id == tenant_id,
                    tenant_db_connections.c.id == connection_id,
                )
                .values(
                    status="ok" if ok else "error",
                    last_tested_at=sa.func.now(),
                    last_test_error=None if ok else error,
                    updated_at=sa.func.now(),
                )
            )


def _row(row: sa.engine.RowMapping) -> ConnectionRow:
    return ConnectionRow(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        adapter_kind=row["adapter_kind"],
        host=row["host"],
        port=row["port"],
        database=row["database"],
        username=row["username"],
        encrypted_secret=row["encrypted_secret"],
        options=row["options_json"] or {},
        status=row["status"],
        last_tested_at=row["last_tested_at"],
        last_test_error=row["last_test_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
