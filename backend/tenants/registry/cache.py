"""Bounded LRU cache for per-tenant registry views.

The cache stores ``TenantRegistryView`` instances keyed by ``tenant_id``.
Eviction is strictly by least-recently-accessed order so memory stays
bounded even with thousands of registered tenants.

Why a custom LRU instead of ``functools.lru_cache``?

* ``functools.lru_cache`` only works on plain functions / methods, not
  awaitable factories.
* We need ``invalidate(tenant_id)`` so admin endpoints can drop the cached
  view immediately after a YAML edit, and ``stats()`` for observability.
"""
from __future__ import annotations

import asyncio
import dataclasses
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID


@dataclass
class TenantRegistryView:
    """Lightweight projection of a tenant's registry.

    Holds parsed JSON, NOT raw YAML strings, so we don't pay re-parsing
    cost on every cache hit. The original YAML lives in the DB and is only
    streamed when an admin explicitly opens an entry for editing.
    """

    tenant_id: UUID
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
    enums: dict[str, dict[str, Any]] = field(default_factory=dict)
    patterns: dict[str, dict[str, Any]] = field(default_factory=dict)
    widgets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def stats(self) -> dict[str, int]:
        return {
            "skills": len(self.skills),
            "enums": len(self.enums),
            "patterns": len(self.patterns),
            "widgets": len(self.widgets),
        }


ViewLoader = Callable[[UUID], Awaitable[TenantRegistryView]]


class TenantRegistryCache:
    """Strictly bounded LRU keyed on ``tenant_id``."""

    def __init__(self, *, max_size: int, loader: ViewLoader) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._max_size = max_size
        self._loader = loader
        self._items: "OrderedDict[UUID, TenantRegistryView]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    async def get(self, tenant_id: UUID) -> TenantRegistryView:
        async with self._lock:
            view = self._items.get(tenant_id)
            if view is not None:
                self._items.move_to_end(tenant_id)
                self._hits += 1
                return view
            self._misses += 1

        # Load outside the lock so concurrent requests for *different* tenants
        # don't serialise on a single mutex while we run the loader.
        view = await self._loader(tenant_id)

        async with self._lock:
            self._items[tenant_id] = view
            self._items.move_to_end(tenant_id)
            while len(self._items) > self._max_size:
                evicted_id, _ = self._items.popitem(last=False)
                self._evictions += 1
                if evicted_id == tenant_id:
                    # Should never happen — we just inserted it.
                    break
            return view

    async def invalidate(self, tenant_id: UUID) -> None:
        async with self._lock:
            self._items.pop(tenant_id, None)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "size": len(self._items),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
        }
