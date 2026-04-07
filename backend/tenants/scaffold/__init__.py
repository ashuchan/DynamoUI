"""Tenant-scoped schema scaffolding jobs (Phase 3).

Owns the ``tenant_scaffold_jobs`` table and the background-job runner that
inspects a tenant's DB connection and produces draft skill / pattern /
widget YAML for review. The actual per-adapter inspection is delegated to a
``Scaffolder`` registered against the connection's ``adapter_kind``.
"""
