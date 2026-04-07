"""Tenant-scoped subsystems (Phase 2+).

Each subdirectory owns one resource type managed via the admin portal:
``connections`` (Phase 2), ``registry`` (Phase 4 — tenant YAML store), etc.
All packages here MUST take ``tenant_id`` as an explicit argument at every
DAO/service entry point. See ``docs/MULTI_TENANT_PLAN.md``.
"""
