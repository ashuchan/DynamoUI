"""Service layer for the tenant YAML registry.

* YAML is parsed once on write and stored alongside its source so the
  runtime cache never has to re-parse on lookup.
* The cache is invalidated immediately on every mutation so the next read
  always reflects the latest state.
"""
from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import structlog
import yaml

from backend.tenants.registry.cache import TenantRegistryCache, TenantRegistryView
from backend.tenants.registry.dao import (
    RegistryDAO,
    RegistryRow,
    UnknownResourceTypeError,
)
from backend.tenants.registry.dtos import RegistryEntryRead, RegistryEntrySummary
from backend.tenants.registry.tables import RESOURCE_TABLES

log = structlog.get_logger(__name__)

_RESOURCE_BUCKETS: dict[str, str] = {
    "skill": "skills",
    "enum": "enums",
    "pattern": "patterns",
    "widget": "widgets",
}


class InvalidYAMLError(ValueError):
    pass


class RegistryEntryNotFoundError(LookupError):
    pass


class RegistryService:
    def __init__(self, dao: RegistryDAO, cache: TenantRegistryCache) -> None:
        self._dao = dao
        self._cache = cache

    @staticmethod
    def supported_types() -> list[str]:
        return sorted(RESOURCE_TABLES.keys())

    async def list(
        self, tenant_id: UUID, resource_type: str
    ) -> list[RegistryEntrySummary]:
        rows = await self._dao.list_for_tenant(tenant_id, resource_type)
        return [
            RegistryEntrySummary(
                id=r.id, name=r.name, checksum=r.checksum, updated_at=r.updated_at
            )
            for r in rows
        ]

    async def get(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> RegistryEntryRead:
        row = await self._dao.get_by_name(tenant_id, resource_type, name)
        if row is None:
            raise RegistryEntryNotFoundError(name)
        return _to_read(row)

    async def upsert(
        self,
        *,
        tenant_id: UUID,
        resource_type: str,
        name: str,
        yaml_source: str,
    ) -> RegistryEntryRead:
        try:
            parsed = yaml.safe_load(yaml_source) or {}
        except yaml.YAMLError as exc:
            raise InvalidYAMLError(f"failed to parse YAML: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InvalidYAMLError("top-level YAML must be a mapping")

        checksum = hashlib.sha256(yaml_source.encode("utf-8")).hexdigest()
        row = await self._dao.upsert(
            tenant_id=tenant_id,
            resource_type=resource_type,
            name=name,
            yaml_source=yaml_source,
            parsed_json=parsed,
            checksum=checksum,
        )
        # Invalidate immediately — Phase 4 invariant.
        await self._cache.invalidate(tenant_id)
        return _to_read(row)

    async def delete(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> None:
        deleted = await self._dao.delete(tenant_id, resource_type, name)
        if not deleted:
            raise RegistryEntryNotFoundError(name)
        await self._cache.invalidate(tenant_id)

    # ------------------------------------------------------------------
    # Loader used by the LRU cache
    # ------------------------------------------------------------------
    async def build_view(self, tenant_id: UUID) -> TenantRegistryView:
        view = TenantRegistryView(tenant_id=tenant_id)
        for resource_type in RESOURCE_TABLES:
            rows = await self._dao.list_for_tenant(tenant_id, resource_type)
            bucket = getattr(view, _RESOURCE_BUCKETS[resource_type])
            for r in rows:
                bucket[r.name] = r.parsed_json
        return view


def _to_read(row: RegistryRow) -> RegistryEntryRead:
    return RegistryEntryRead(
        id=row.id,
        tenant_id=row.tenant_id,
        resource_type=row.resource_type,  # type: ignore[arg-type]
        name=row.name,
        yaml_source=row.yaml_source,
        parsed_json=row.parsed_json,
        checksum=row.checksum,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
