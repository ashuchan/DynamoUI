"""DAO for the tenant YAML registry tables.

Every method takes a ``resource_type`` (skill / enum / pattern / widget) and
the appropriate table is selected from ``RESOURCE_TABLES``. There is no
separate DAO per resource type because the columns are identical — the
caller picks the type and the DAO does the rest.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.tenants.registry.tables import RESOURCE_TABLES


@dataclass(frozen=True)
class RegistryRow:
    id: UUID
    tenant_id: UUID
    resource_type: str
    name: str
    yaml_source: str
    parsed_json: dict[str, Any]
    checksum: str
    created_at: datetime
    updated_at: datetime


class UnknownResourceTypeError(KeyError):
    pass


class RegistryDAO:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @staticmethod
    def _table(resource_type: str) -> sa.Table:
        try:
            return RESOURCE_TABLES[resource_type]
        except KeyError as exc:
            raise UnknownResourceTypeError(resource_type) from exc

    async def list_for_tenant(
        self, tenant_id: UUID, resource_type: str
    ) -> list[RegistryRow]:
        table = self._table(resource_type)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(table)
                    .where(table.c.tenant_id == tenant_id)
                    .order_by(table.c.name.asc())
                )
            ).mappings().all()
        return [_to_row(r, resource_type) for r in rows]

    async def get_by_name(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> RegistryRow | None:
        table = self._table(resource_type)
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(table).where(
                        table.c.tenant_id == tenant_id,
                        table.c.name == name,
                    )
                )
            ).mappings().first()
        return _to_row(row, resource_type) if row else None

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        resource_type: str,
        name: str,
        yaml_source: str,
        parsed_json: dict[str, Any],
        checksum: str,
    ) -> RegistryRow:
        table = self._table(resource_type)
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    sa.select(table.c.id).where(
                        table.c.tenant_id == tenant_id,
                        table.c.name == name,
                    )
                )
            ).first()
            if existing is None:
                await conn.execute(
                    sa.insert(table).values(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        name=name,
                        yaml_source=yaml_source,
                        parsed_json=parsed_json,
                        checksum=checksum,
                    )
                )
            else:
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == existing[0])
                    .values(
                        yaml_source=yaml_source,
                        parsed_json=parsed_json,
                        checksum=checksum,
                        updated_at=sa.func.now(),
                    )
                )
        row = await self.get_by_name(tenant_id, resource_type, name)
        assert row is not None
        return row

    async def delete(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> bool:
        table = self._table(resource_type)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(table).where(
                    table.c.tenant_id == tenant_id,
                    table.c.name == name,
                )
            )
        return result.rowcount > 0


def _to_row(row: sa.engine.RowMapping, resource_type: str) -> RegistryRow:
    return RegistryRow(
        id=row["id"],
        tenant_id=row["tenant_id"],
        resource_type=resource_type,
        name=row["name"],
        yaml_source=row["yaml_source"],
        parsed_json=row["parsed_json"],
        checksum=row["checksum"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
