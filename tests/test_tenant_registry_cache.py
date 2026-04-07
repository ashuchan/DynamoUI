"""Tests for the bounded LRU TenantRegistryCache.

Memory bound is enforced under load: when N tenants > cache size, the cache
must never grow beyond ``max_size``. The Phase 4 plan handoff explicitly
calls this out as the invariant the cache exists to provide.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend.tenants.registry.cache import TenantRegistryCache, TenantRegistryView


def _view_for(tenant_id: UUID) -> TenantRegistryView:
    return TenantRegistryView(tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_get_loads_and_caches() -> None:
    calls: list[UUID] = []

    async def loader(tid: UUID) -> TenantRegistryView:
        calls.append(tid)
        return _view_for(tid)

    cache = TenantRegistryCache(max_size=4, loader=loader)
    tenant = uuid4()

    a = await cache.get(tenant)
    b = await cache.get(tenant)
    assert a is b  # cached instance
    assert calls == [tenant]


@pytest.mark.asyncio
async def test_eviction_is_strict_lru() -> None:
    async def loader(tid: UUID) -> TenantRegistryView:
        return _view_for(tid)

    cache = TenantRegistryCache(max_size=3, loader=loader)
    t1, t2, t3, t4 = uuid4(), uuid4(), uuid4(), uuid4()
    await cache.get(t1)
    await cache.get(t2)
    await cache.get(t3)
    # Touch t1 so it's most-recently-used; t2 should evict next.
    await cache.get(t1)
    await cache.get(t4)

    stats = cache.stats()
    assert stats["size"] == 3
    assert stats["evictions"] == 1


@pytest.mark.asyncio
async def test_memory_bounded_under_load() -> None:
    async def loader(tid: UUID) -> TenantRegistryView:
        return _view_for(tid)

    cache = TenantRegistryCache(max_size=8, loader=loader)
    for _ in range(500):
        await cache.get(uuid4())
    assert cache.stats()["size"] == 8


@pytest.mark.asyncio
async def test_invalidate_drops_entry() -> None:
    call_count = 0

    async def loader(tid: UUID) -> TenantRegistryView:
        nonlocal call_count
        call_count += 1
        return _view_for(tid)

    cache = TenantRegistryCache(max_size=4, loader=loader)
    tenant = uuid4()
    await cache.get(tenant)
    await cache.invalidate(tenant)
    await cache.get(tenant)
    assert call_count == 2


@pytest.mark.asyncio
async def test_invalid_max_size_rejected() -> None:
    async def loader(tid: UUID) -> TenantRegistryView:
        return _view_for(tid)

    with pytest.raises(ValueError):
        TenantRegistryCache(max_size=0, loader=loader)


@pytest.mark.asyncio
async def test_view_reports_resource_counts() -> None:
    view = TenantRegistryView(tenant_id=uuid4())
    view.skills["employee"] = {"name": "Employee"}
    view.enums["role"] = {"values": []}
    assert view.stats() == {"skills": 1, "enums": 1, "patterns": 0, "widgets": 0}
