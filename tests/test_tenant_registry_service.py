"""Service-level tests for the tenant YAML registry.

Uses an in-memory DAO so tests don't need PostgreSQL. The key invariants
that Phase 4 promises are exercised:

* YAML is parsed and a checksum is computed on write.
* Mutations invalidate the LRU cache so the next read is fresh.
* Cross-tenant get / list / delete is impossible.
* Invalid YAML is rejected with InvalidYAMLError before the DAO is touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from backend.tenants.registry.cache import TenantRegistryCache, TenantRegistryView
from backend.tenants.registry.dao import RegistryRow
from backend.tenants.registry.service import (
    InvalidYAMLError,
    RegistryEntryNotFoundError,
    RegistryService,
)


class FakeRegistryDAO:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str, str], RegistryRow] = {}

    async def list_for_tenant(
        self, tenant_id: UUID, resource_type: str
    ) -> list[RegistryRow]:
        return [
            r
            for (t, rt, _name), r in self.rows.items()
            if t == tenant_id and rt == resource_type
        ]

    async def get_by_name(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> RegistryRow | None:
        return self.rows.get((tenant_id, resource_type, name))

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
        key = (tenant_id, resource_type, name)
        existing = self.rows.get(key)
        now = datetime.now(timezone.utc)
        row = RegistryRow(
            id=existing.id if existing else uuid4(),
            tenant_id=tenant_id,
            resource_type=resource_type,
            name=name,
            yaml_source=yaml_source,
            parsed_json=parsed_json,
            checksum=checksum,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.rows[key] = row
        return row

    async def delete(
        self, tenant_id: UUID, resource_type: str, name: str
    ) -> bool:
        return self.rows.pop((tenant_id, resource_type, name), None) is not None


@pytest.fixture
def dao() -> FakeRegistryDAO:
    return FakeRegistryDAO()


@pytest.fixture
def service(dao: FakeRegistryDAO) -> RegistryService:
    svc = RegistryService(dao=dao, cache=None)  # type: ignore[arg-type]
    cache = TenantRegistryCache(max_size=4, loader=svc.build_view)
    svc._cache = cache  # type: ignore[attr-defined]
    return svc


SAMPLE_YAML = """
name: Employee
fields:
  - name: id
    type: integer
""".strip()


@pytest.mark.asyncio
async def test_upsert_parses_and_invalidates(service: RegistryService) -> None:
    tenant = uuid4()
    cache = service._cache  # type: ignore[attr-defined]

    # Warm the cache before the upsert.
    await cache.get(tenant)
    assert cache.stats()["size"] == 1

    entry = await service.upsert(
        tenant_id=tenant,
        resource_type="skill",
        name="employee",
        yaml_source=SAMPLE_YAML,
    )
    assert entry.parsed_json["name"] == "Employee"
    assert len(entry.checksum) == 64  # sha256 hex
    # Cache should have been invalidated → size returns to 0.
    assert cache.stats()["size"] == 0


@pytest.mark.asyncio
async def test_invalid_yaml_rejected(service: RegistryService) -> None:
    with pytest.raises(InvalidYAMLError):
        await service.upsert(
            tenant_id=uuid4(),
            resource_type="skill",
            name="bad",
            yaml_source="::: not yaml :::\n\t- bad",
        )


@pytest.mark.asyncio
async def test_top_level_must_be_mapping(service: RegistryService) -> None:
    with pytest.raises(InvalidYAMLError):
        await service.upsert(
            tenant_id=uuid4(),
            resource_type="skill",
            name="list",
            yaml_source="- one\n- two",
        )


@pytest.mark.asyncio
async def test_cross_tenant_get_denied(service: RegistryService) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    await service.upsert(
        tenant_id=tenant_a,
        resource_type="skill",
        name="employee",
        yaml_source=SAMPLE_YAML,
    )
    with pytest.raises(RegistryEntryNotFoundError):
        await service.get(tenant_b, "skill", "employee")


@pytest.mark.asyncio
async def test_list_only_returns_calling_tenant(service: RegistryService) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    await service.upsert(
        tenant_id=tenant_a, resource_type="skill", name="a", yaml_source=SAMPLE_YAML
    )
    await service.upsert(
        tenant_id=tenant_b, resource_type="skill", name="b", yaml_source=SAMPLE_YAML
    )
    assert {e.name for e in await service.list(tenant_a, "skill")} == {"a"}
    assert {e.name for e in await service.list(tenant_b, "skill")} == {"b"}


@pytest.mark.asyncio
async def test_build_view_groups_by_resource_type(service: RegistryService) -> None:
    tenant = uuid4()
    await service.upsert(
        tenant_id=tenant, resource_type="skill", name="employee", yaml_source=SAMPLE_YAML
    )
    await service.upsert(
        tenant_id=tenant,
        resource_type="enum",
        name="role",
        yaml_source="values: [admin, member]\n",
    )
    view = await service.build_view(tenant)
    assert "employee" in view.skills
    assert "role" in view.enums
    assert view.stats() == {"skills": 1, "enums": 1, "patterns": 0, "widgets": 0}


@pytest.mark.asyncio
async def test_delete_invalidates_cache(service: RegistryService) -> None:
    tenant = uuid4()
    cache = service._cache  # type: ignore[attr-defined]
    await service.upsert(
        tenant_id=tenant, resource_type="skill", name="employee", yaml_source=SAMPLE_YAML
    )
    await cache.get(tenant)
    assert cache.stats()["size"] == 1
    await service.delete(tenant, "skill", "employee")
    assert cache.stats()["size"] == 0


@pytest.mark.asyncio
async def test_delete_missing_raises(service: RegistryService) -> None:
    with pytest.raises(RegistryEntryNotFoundError):
        await service.delete(uuid4(), "skill", "ghost")
