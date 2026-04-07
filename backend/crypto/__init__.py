"""Crypto subsystem — AES-GCM envelope encryption for DB-stored secrets.

Phase 2 of the multi-tenant rollout (see ``docs/MULTI_TENANT_PLAN.md``).

Every secret persisted in the internal schema (DB connection passwords, OAuth
client secrets, service account JSON) MUST go through ``envelope.encrypt`` /
``envelope.decrypt``. Plaintext secrets must never reach the database.
"""
