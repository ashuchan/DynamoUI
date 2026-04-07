"""Tenant-scoped YAML registry (Phase 4).

Stores skills, enums, patterns and widgets in the internal database, scoped
per tenant. Runtime lookups go through ``TenantRegistryCache`` — a bounded
LRU keyed on ``tenant_id`` so memory stays predictable even with thousands
of registered tenants.

See ``docs/MULTI_TENANT_PLAN.md`` Phase 4.
"""
