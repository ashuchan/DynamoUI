# Multi-Tenant Authentication & Admin Portal — Phased Rollout Plan

> Authoritative roadmap for delivering the multi-tenant auth + admin portal work
> across the DynamoUI backend and DynamoUI-frontend repos. Every phase lists its
> scope, acceptance criteria, and a **Handoff** block that the next Claude Code
> session MUST read before starting.
>
> All phases are developed on branch `claude/auth-multi-tenant-setup-znYlQ` in
> both repos. The merge request is only opened after the final phase.

---

## Guiding Rules (apply to every phase)

1. **Tenant segregation is non-negotiable.** Every query touching internal
   tables must filter by `tenant_id`. Every service function takes
   `tenant_id` as an explicit argument — never read it from an ambient context
   at the data-access layer. Cross-tenant access is a hard error and must be
   covered by a unit test in the same phase that introduces the code.
2. **No regressions.** `pytest` and `npm test` must remain green at the end of
   every phase. When a change touches existing modules, add a regression test
   that pins pre-existing behaviour before modifying it.
3. **Keep memory footprint bounded.** YAML registries are loaded on demand per
   tenant via an LRU cache (see Phase 4), never as a single global dictionary.
   Do not widen any global registry during Phases 1–3.
4. **Encryption at rest.** Any credential stored in the DB (DB connection
   passwords, OAuth client secrets, service-account JSON) must be encrypted
   using the AES-GCM envelope helper introduced in Phase 2. Plaintext
   credentials MUST NOT hit the database or logs.
5. **Settings.** Every new knob is a Pydantic Setting with `DYNAMO_AUTH_*`,
   `DYNAMO_CRYPTO_*`, or `DYNAMO_TENANT_*` env prefix. No new globals.
6. **Tests alongside code.** Each phase ships its own `tests/test_<area>.py`
   file. Coverage target for the new modules: **90% lines**.

---

## Phase 1 — Auth Foundation (✅ shipped in this session)

**Goal:** Users can sign up / sign in using email+password or Google OAuth. A
signup creates a personal tenant with the user as owner. JWT carries a
`tenant_id` claim used by downstream services.

### Scope

- `backend/auth/` package with layered structure (models → dao → service → api).
- Internal tables (in `dynamoui_internal` schema) created via Alembic:
  `tenants`, `users`, `tenant_users`, `oauth_identities`.
  Designed for future N:M — a user can belong to multiple tenants; signup
  creates exactly one personal tenant with role `owner`.
- `security.py`: scrypt-based password hashing (stdlib only — no new deps) +
  JWT helpers built on `python-jose` (already a dependency).
- `AuthSettings` (`DYNAMO_AUTH_*`): JWT lifetime, Google OAuth client id /
  secret, allowed redirect URIs, signup-enabled flag.
- REST endpoints under `/api/v1/auth`:
  - `POST /signup` — email + password + display_name → new tenant + user + JWT.
  - `POST /login` — email + password → JWT.
  - `POST /google` — verify a Google ID token → existing or new user + JWT.
  - `GET  /me` — current user + tenant summary.
- `dependencies.py`: `current_user` and `current_tenant` FastAPI dependencies
  that decode the JWT, look up the user, and enforce tenant consistency.
- Frontend:
  - `AuthContext` + `useAuth` hook with tokens persisted in `localStorage`.
  - `apiClient` now attaches `Authorization: Bearer <token>` and exposes the
    new auth endpoints.
  - `SignInPage`, `SignUpPage`, `ProtectedRoute` components.
  - `App.tsx` renders the auth screen when the user is unauthenticated and the
    existing dashboard otherwise.
- Tests:
  - `tests/test_auth.py` covers: password hash roundtrip, JWT encode/decode,
    signup creates tenant + membership, duplicate email rejected, login
    succeeds + fails, Google flow with a mocked verifier, `current_user`
    dependency rejects invalid/expired tokens, cross-tenant membership lookup
    is denied.
  - `tests/test_auth_dao.py` covers tenant isolation on the DAO layer.

### Acceptance

- `pytest` green (incl. new auth tests).
- `alembic upgrade head` succeeds against a fresh PostgreSQL.
- Frontend builds (`npm run build`) without TypeScript errors.
- Existing skill/pattern/adapter behaviour unchanged (no routes removed, no
  signatures altered).

### Handoff → Phase 2

**Read first:** `backend/auth/models/tables.py`, `backend/auth/dependencies.py`,
`docs/MULTI_TENANT_PLAN.md` (this file).

**Invariants Phase 2 must preserve:**

- Use `current_tenant` dependency for every new tenant-scoped route.
- Do not widen JWT claims without updating `security.decode_token` AND adding
  a migration for any new persisted fields.
- The `tenant_users.role` column is the single source of truth for
  authorisation. Do not invent a parallel scheme.
- `_google_verifier` in `backend/auth/service.py` is injected — keep mocking
  it in tests rather than calling Google's token endpoint live.

---

## Phase 2 — Tenant-Scoped DB Connection Registry + Encryption

**Goal:** Tenants can register and manage database connections through the
admin API. Credentials are encrypted at rest. All reads/writes are scoped to
the calling tenant.

### Scope

- New table `tenant_db_connections` (tenant_id, id, name, adapter_kind, host,
  port, database, username, encrypted_secret, options_json, status, created_at,
  updated_at, last_tested_at) with `UNIQUE (tenant_id, name)` and a FK to
  `tenants`.
- `backend/crypto/envelope.py`: AES-GCM helper with a KEK loaded from
  `DYNAMO_CRYPTO_MASTER_KEY` (base64-encoded 32 bytes). Per-record DEK
  wrapping + `cryptography` library (add as a new runtime dependency).
- `backend/tenants/connections/` package: dao, service, pydantic DTOs,
  REST router. Only authenticated tenant owners can call mutating endpoints.
- Endpoints under `/api/v1/admin/connections`:
  `GET`, `POST`, `PATCH /{id}`, `DELETE /{id}`, `POST /{id}/test`.
- Adapter registry is **no longer** read from `adapters.registry.yaml` on
  startup; instead, `AdapterRegistry` becomes a per-tenant view materialised
  from the DB on first use (see caching rules in Phase 4).
- Tests: round-trip encryption, cross-tenant read is denied, connection-test
  endpoint catches bad credentials without leaking them in logs.

### Handoff → Phase 3

- Never store `password` in a Pydantic response model — only
  `has_password: bool`.
- The envelope helper’s `encrypt` / `decrypt` functions are the ONLY place
  `cryptography.hazmat` is imported. Future phases must reuse it.
- Preserve the `adapters.registry.yaml` loader as a backwards-compat path
  for Phase 2 only; delete it in Phase 5.

---

## Phase 3 — Admin Portal UI + DB Schema Scaffolding

**Goal:** Frontend admin portal lets tenants manage connections, scaffold
schemas, and review generated skill/pattern/widget configs.

### Scope

- New frontend route `/admin` gated by `tenant_users.role in ('owner','admin')`.
- Pages: `ConnectionsList`, `ConnectionForm`, `ConnectionTest`, `ScaffoldJob`.
- Backend endpoint `POST /api/v1/admin/connections/{id}/scaffold` that invokes
  the existing `schema_inspector.py` logic, streams results as a job, and
  persists a draft skill-set tied to the tenant.
- Background job table `tenant_scaffold_jobs` (tenant_id, job_id, status,
  progress, result_summary, error).
- A "Review & Apply" step where generated skills are presented as editable
  YAML in the browser (Monaco editor) before they are committed into the
  tenant's internal registry (Phase 4).

### Handoff → Phase 4

- Scaffold jobs must NOT write to the shared on-disk `skills/` directory. All
  generated artefacts live in tenant-scoped storage prepared in Phase 4.
- The Monaco editor component must lazy-load to avoid bloating the auth
  bundle.

---

## Phase 4 — Internal YAML Registry (tenant-scoped) + Runtime Cache

**Goal:** Skills, patterns, widgets are stored per-tenant in the internal DB
schema. Runtime boot loads only the calling tenant's configs into an LRU
cache, not the full global set.

### Scope

- New tables: `tenant_skills`, `tenant_enums`, `tenant_patterns`,
  `tenant_widgets`, all with `tenant_id` + `name` UNIQUE, `yaml_source TEXT`,
  `parsed_json JSONB`, `checksum`, `updated_at`.
- `backend/skill_registry/tenant_loader.py` — replaces the on-disk YAML
  discovery for tenant-owned configs. Still supports the on-disk source for
  "platform defaults" that ship with the framework.
- `TenantRegistryCache` (in-process LRU, default 64 tenants, eviction by
  last-access). The cache stores a lightweight `TenantRegistryView` — NOT a
  full copy of every YAML string. Pattern cache is rebuilt on demand per
  tenant using shared `FuzzyMatcher` code paths.
- Startup no longer calls `discover_all()` for tenant configs. Platform
  default configs are still loaded once globally and *merged* into the
  per-tenant view at lookup time.
- Cache invalidation: `TenantRegistryCache.invalidate(tenant_id)` is called
  from the admin endpoints whenever a YAML is added / edited / deleted.
- Frontend review-and-edit UI (Skills / Patterns / Widgets tabs under the
  admin portal) talks to the new `/api/v1/admin/registry/*` endpoints.

### Handoff → Phase 5

- `TenantRegistryView` MUST NOT hold references to file paths — the model has
  moved off disk and into the DB.
- The LRU cache's max size is configurable via
  `DYNAMO_TENANT_REGISTRY_CACHE_SIZE` and must be tested with a stress
  fixture to ensure bounded memory.

---

## Phase 5 — Cloud DB Adapters (DynamoDB, Spanner, Oracle, Cosmos DB…)

**Goal:** Tenants can add cloud-managed database connections.

### Scope

- New adapter packages under `backend/adapters/`:
  - `dynamodb/` (AWS; boto3 client, no `sa.Table`, schema inspected via
    `describe_table`).
  - `spanner/` (GCP; `google-cloud-spanner`).
  - `oracle/` (oracledb driver in thin mode).
  - `cosmosdb/` (Azure; `azure-cosmos`).
  - `bigquery/`, `redshift/`, `snowflake/` — stretch targets.
- Every new adapter implements `DataAdapter`, `SchemaInspector`, and
  `SchemaValidator` and registers a `kind` in `backend/adapters/kinds.py`.
- Adapter-specific credential shapes are validated by the connections service
  and encrypted exactly once (via Phase 2 envelope helper).
- Remove the on-disk `adapters.registry.yaml` loader (scheduled deletion in
  this phase).
- Each adapter ships isolated tests that mock the cloud SDK — no network
  calls in CI.

### Handoff → Phase 6

- All new adapters must implement `close()` / `dispose()` so the lifespan
  shutdown hook can release pools.
- Every new cloud SDK is gated behind an `extras_require` entry in
  `pyproject.toml`; do not make them mandatory.

---

## Phase 6 — Merge Request & Release Hardening

**Goal:** Open the MR to `main` and get it review-ready.

### Scope

- Run full CI matrix (backend + frontend + alembic).
- Generate release notes summarising every phase.
- Document rollback steps for each Alembic migration.
- Open the PR against `main` on both repos; link them to each other.

---

## Cross-cutting Checklist (touch at end of every phase)

- [ ] `pytest` green
- [ ] `npm run build` green
- [ ] `alembic upgrade head` idempotent
- [ ] New docs appended to this file's Handoff section
- [ ] No new global in-memory dict keyed by tenant (use the LRU cache)
- [ ] No plaintext secrets in logs / responses / DB
