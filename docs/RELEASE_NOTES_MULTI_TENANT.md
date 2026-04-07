# Multi-Tenant + Auth + Cloud Adapters — Release Notes

This release lands the full six-phase rollout described in
``docs/MULTI_TENANT_PLAN.md``. Every feature shipped on `main` via
phase-by-phase merges; this document summarises the surface area, the
operational impact, and the rollback story per migration.

---

## What's new

### Authentication (Phase 1)

* Email + password sign-up / sign-in.
* Google OAuth via ID token verification.
* Per-tenant JWTs (`sub`, `tid`, `email`, `role`, `iat`, `exp`).
* `current_user` / `current_tenant` / `require_role` FastAPI dependencies.
* Frontend auth context with token rehydration and a unified sign-in screen
  rendered behind `<ProtectedRoute>`.
* New endpoints under `/api/v1/auth`:
  `POST /signup`, `POST /login`, `POST /google`, `GET /me`.

### Tenant DB connection registry + encryption (Phase 2)

* AES-256-GCM envelope encryption with per-record DEK wrapping
  (`backend/crypto/envelope.py`). The KEK is loaded from
  `DYNAMO_CRYPTO_MASTER_KEY`.
* `tenant_db_connections` table — secrets are stored as opaque envelopes,
  responses never include plaintext passwords (`has_password: bool` only).
* `/api/v1/admin/connections` CRUD + `POST /{id}/test`.

### Admin portal + scaffold jobs (Phase 3)

* React admin portal with Connections, Scaffold Jobs and Registry tabs.
* `tenant_scaffold_jobs` table tracks async schema-inspection jobs.
* `POST /api/v1/admin/connections/{id}/scaffold` queues a job that runs in
  a `BackgroundTasks` callback. Decrypted credentials never cross a request
  handler boundary.
* Pluggable `Scaffolder` protocol so adapter modules can plug in inspection
  logic without touching the orchestration.

### Tenant YAML registry + bounded LRU cache (Phase 4)

* `tenant_skills`, `tenant_enums`, `tenant_patterns`, `tenant_widgets`
  store YAML source plus parsed JSON projections, scoped per tenant.
* `TenantRegistryCache` is an LRU keyed on `tenant_id` (default size 64,
  configurable via `DYNAMO_TENANT_REGISTRY_CACHE_SIZE`). Memory is
  bounded under load (covered by `tests/test_tenant_registry_cache.py`).
* Mutations always invalidate the calling tenant's cache entry.
* `/api/v1/admin/registry/{type}` GET/PUT/DELETE endpoints. Reads are
  open to members; writes require owner / admin.

### Cloud adapters (Phase 5)

* DynamoDB, Spanner, Oracle, Cosmos DB adapter packages with:
  * `CloudDataAdapter` stubs that fail loudly with `NotImplementedError`
    so callers can't get silent empty results.
  * Connection testers with injectable client / connection factories
    (zero cloud-SDK calls in CI).
  * Lazy SDK imports gated behind clear `pip install` hints.
* Cloud SDKs are `extras_require` entries in `pyproject.toml` —
  `pip install dynamoui[dynamodb,spanner,oracle,cosmosdb]` to opt in.
* `register_cloud_adapters()` wires every tester + the DynamoDB scaffolder
  into the connection + scaffold services at startup.

---

## Settings cheat sheet

| Variable | Default | Purpose |
|---|---|---|
| `DYNAMO_AUTH_JWT_SECRET` | dev placeholder | HS256 signing secret. **Set in prod.** |
| `DYNAMO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | `3600` | JWT lifetime |
| `DYNAMO_AUTH_GOOGLE_CLIENT_ID` | empty | Required to enable Google login |
| `DYNAMO_AUTH_SIGNUP_ENABLED` | `true` | Toggle public signups |
| `DYNAMO_CRYPTO_MASTER_KEY` | empty | Base64 32 bytes. **Required for connection passwords.** |
| `DYNAMO_CRYPTO_KEY_VERSION` | `1` | Bump on key rotation |
| `DYNAMO_TENANT_REGISTRY_CACHE_SIZE` | `64` | Max cached tenant registry views |

Generate a master key:

```bash
python -c "from backend.crypto.envelope import generate_master_key; print(generate_master_key())"
```

---

## Migration history

| Revision | File | Tables |
|---|---|---|
| `001_metering` | `20260406_001_create_metering_tables.py` | metering_* |
| `002_auth` | `20260407_002_create_auth_tables.py` | auth_tenants, auth_users, auth_tenant_users, auth_oauth_identities |
| `003_tenant_connections` | `20260408_003_create_tenant_connections.py` | tenant_db_connections |
| `004_scaffold_jobs` | `20260408_004_create_tenant_scaffold_jobs.py` | tenant_scaffold_jobs |
| `005_tenant_registry` | `20260408_005_create_tenant_registry.py` | tenant_skills, tenant_enums, tenant_patterns, tenant_widgets |

Apply forward:

```bash
alembic upgrade head
```

### Rollback

Each migration ships a `downgrade()` that reverses the corresponding
`upgrade()`. Rollback is **single-step** — never run a multi-step downgrade
without a fresh PostgreSQL backup.

```bash
# Roll back one migration
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade 003_tenant_connections

# Full rollback to pre-Phase-1 state
alembic downgrade 001_metering
```

**Cascade implications:** dropping `auth_tenants` cascades to every
tenant-scoped table that holds an FK back to it (`auth_tenant_users`,
`tenant_db_connections`, `tenant_scaffold_jobs`, `tenant_*` registry
tables). Always take a backup before dropping any table from the auth
chain.

### Rollback runbook (per phase)

1. **Take a fresh logical backup** of the `dynamoui_internal` schema.
2. **Stop traffic to mutation endpoints** for the duration of the rollback.
3. Run `alembic downgrade <revision>` to the previous step.
4. Re-deploy the matching application revision (the schema is unforgiving:
   the new code expects the new tables).
5. Re-enable traffic.
6. Confirm `pytest` is green against the rolled-back code revision.

---

## Operational impact

* **No regressions** — existing skill / pattern / widget / metering routes
  are untouched. Every new route is namespaced under `/api/v1/auth` or
  `/api/v1/admin/...`.
* **JWT enforcement is opt-in** at the route level. Existing routes are
  unchanged. As tenant migration progresses, individual routers will
  re-mount themselves behind `Depends(get_current_tenant)`.
* **Memory footprint is bounded.** The Phase 4 LRU cache caps in-memory
  tenant views (default 64 tenants, configurable). Tested under
  500-tenant churn — eviction order is strict LRU.
* **Cloud SDKs are optional.** Production images can install only the
  cloud kinds the tenant base actually uses.

---

## Test coverage matrix

| Area | File |
|---|---|
| Password hashing + JWT | `tests/test_auth_security.py` |
| Auth service (signup, login, Google, isolation) | `tests/test_auth_service.py` |
| AES-GCM envelope helper | `tests/test_crypto_envelope.py` |
| Tenant connection service + isolation | `tests/test_tenant_connections.py` |
| Scaffold orchestration + isolation | `tests/test_tenant_scaffold.py` |
| LRU registry cache (eviction, bounded memory) | `tests/test_tenant_registry_cache.py` |
| Registry service (YAML parse, invalidation, isolation) | `tests/test_tenant_registry_service.py` |
| Cloud adapters (DynamoDB / Spanner / Oracle / Cosmos) | `tests/test_cloud_adapters.py` |

Run the suite locally:

```bash
pytest -q
```

The new tests use in-memory fakes — no PostgreSQL or cloud SDK is required
to execute them.
