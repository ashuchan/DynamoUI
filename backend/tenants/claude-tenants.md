# claude-tenants

Module: `backend/tenants/`
Role: Tenant-scoped subsystems — DB connections (Phase 2), scaffold jobs (Phase 3), YAML registry + runtime LRU cache (Phase 4).

## Subpackages

| Subpackage | Phase | Owns |
|---|---|---|
| `connections/` | 2 | `tenant_db_connections` — encrypted connection registry |
| `scaffold/` | 3 | `tenant_scaffold_jobs` — async schema-inspection jobs |
| `registry/` | 4 | `tenant_skills`, `tenant_enums`, `tenant_patterns`, `tenant_widgets` + `TenantRegistryCache` |

## Shared rules — applies to every file in this tree

1. **Every DAO method takes `tenant_id` explicitly.** Never read it from context. Never pass it positionally — always keyword-argument for clarity.
2. **Cross-tenant access is impossible by construction.** Every query has a `WHERE tenant_id = :tenant_id` clause. Every new method MUST have a unit test that proves Tenant B can't see Tenant A's rows. Patterns to copy: `tests/test_tenant_connections.py`, `tests/test_tenant_scaffold.py`, `tests/test_tenant_registry_service.py`.
3. **Responses never include plaintext credentials.** Use `has_password: bool` on the read DTOs. The decrypted dict from `ConnectionService.materialise(row)` is single-use — pass it straight to the adapter, don't log or serialise it.
4. **Registry mutations invalidate the LRU cache.** `RegistryService.upsert` / `delete` call `cache.invalidate(tenant_id)` before returning. Never skip.

## `connections/`

- `ConnectionService.register_tester(adapter_kind, tester)` — adapters call this at startup (see `backend/adapters/cloud_registry.py`) to plug in connectivity tests. The tester signature is `async (materialised_connection: dict) -> str | None` (None = success).
- `ConnectionService.materialise(row)` is the **only** place `backend.crypto.envelope.decrypt` is called outside `crypto/` itself.
- `tenant_db_connections.encrypted_secret` is `TEXT` — it stores the full envelope JSON. Never store it as `VARCHAR(N)`.

## `scaffold/`

- `ScaffoldService.register_scaffolder(adapter_kind, scaffolder)` — same pattern as the tester registry.
- `ScaffoldService.run(...)` is the **only** function allowed to materialise a connection inside a background task. Do not pass decrypted credentials across `BackgroundTasks.add_task(...)` argument boundaries — materialise inside the task, not in the request handler.
- Jobs progress through `pending → running → completed | failed`. The `run()` method always writes a terminal state; any exception is caught, logged (no credential fields), and recorded as `failed`.

## `registry/`

- `tenant_skills` / `tenant_enums` / `tenant_patterns` / `tenant_widgets` share the same column layout — a single DAO (`RegistryDAO`) drives all four via `RESOURCE_TABLES`.
- **Parse once on write, not on read.** Upsert runs `yaml.safe_load`, stores `parsed_json` alongside `yaml_source`, and computes a SHA-256 `checksum`. Runtime cache lookups never re-parse.
- `TenantRegistryCache` is a strictly bounded LRU keyed on `tenant_id` (default 64, via `DYNAMO_TENANT_REGISTRY_CACHE_SIZE`). It loads outside the lock so concurrent requests for different tenants don't serialise on one mutex. Tested under 500-tenant churn in `tests/test_tenant_registry_cache.py`.
- **Do not add any other global dict keyed on `tenant_id`.** If you need per-tenant caching, extend `TenantRegistryCache` or follow the same LRU pattern — never an unbounded dict.
