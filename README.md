# DynamoUI

LLM-powered UI generation for backend data models. Define your database schema as YAML skill files — DynamoUI serves query, filter, and mutation interfaces with no frontend code required.

DynamoUI is **multi-tenant**: every user gets a personal tenant on sign-up, connects their own databases via an encrypted connection registry, scaffolds skills on demand, and edits YAML configs through an admin portal. See [`docs/MULTI_TENANT_PLAN.md`](docs/MULTI_TENANT_PLAN.md) and [`docs/RELEASE_NOTES_MULTI_TENANT.md`](docs/RELEASE_NOTES_MULTI_TENANT.md) for the full rollout, migration history, and rollback runbook.

## Tech Stack

- **Python 3.11+** — FastAPI, Pydantic v2, SQLAlchemy 2, asyncpg
- **PostgreSQL** — internal schema (auth, metering, tenant registry) + default business-data adapter
- **Cloud adapters** (opt-in) — DynamoDB, Spanner, Oracle, Cosmos DB
- **Auth** — email+password (stdlib scrypt) + Google OAuth → per-tenant JWTs (`python-jose`)
- **Encryption** — AES-256-GCM envelope with per-record DEK wrapping (`cryptography`)
- **Claude / Gemini** — LLM-driven query synthesis and pattern seeding

## Setup

```bash
git clone https://github.com/ashuchan/DynamoUI.git
cd DynamoUI

python -m venv venv
.\venv\Scripts\activate          # Windows
# source venv/bin/activate       # macOS/Linux

python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

Verify the install:

```bash
pytest --cov
```

## Configuring PostgreSQL

DynamoUI reads all connection parameters from environment variables prefixed `DYNAMO_PG_`. The easiest way to set these up is with the interactive setup command:

```bash
dynamoui setup
```

This prompts for your host, port, database name, read user, write user, and SSL mode, tests the connection, then writes a `.env` file in the project root. All settings classes load `.env` automatically on startup.

To skip the connection test (e.g. when the DB isn't running yet):

```bash
dynamoui setup --no-test
```

### Manual configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_PG_HOST` | `localhost` | Database host |
| `DYNAMO_PG_PORT` | `5432` | Database port |
| `DYNAMO_PG_DATABASE` | `dynamoui` | Database name |
| `DYNAMO_PG_USER` | `dynamoui_reader` | Read-only user for SELECT queries |
| `DYNAMO_PG_PASSWORD` | _(required)_ | Read user password |
| `DYNAMO_PG_WRITE_USER` | `dynamoui_writer` | Write user for mutations |
| `DYNAMO_PG_WRITE_PASSWORD` | _(required)_ | Write user password |
| `DYNAMO_PG_SSL_MODE` | `prefer` | Use `require` in production |

Real environment variables always take precedence over `.env`. See `.env.example` for the full list including pool sizing and cache settings.

## Configuring auth + encryption

Authentication, the tenant connection registry, and the tenant YAML registry all live in the `dynamoui_internal` schema. Apply the Alembic migrations first (see [Internal schema setup](#internal-schema-setup)), then set the auth + crypto env vars.

### Auth (`DYNAMO_AUTH_*`)

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_AUTH_JWT_SECRET` | dev placeholder | HS256 signing secret for access tokens. **Required in production.** |
| `DYNAMO_AUTH_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `DYNAMO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | `3600` | Access token lifetime (default 1h) |
| `DYNAMO_AUTH_SIGNUP_ENABLED` | `true` | Toggle public signups on/off |
| `DYNAMO_AUTH_GOOGLE_CLIENT_ID` | _(empty)_ | Google OAuth client id. Empty disables Google login. |
| `DYNAMO_AUTH_SCRYPT_N` | `16384` | Password hashing CPU/memory cost |

JWTs carry `sub` (user id), `tid` (active tenant id), `email`, `role`, `iat`, `exp`. Routes protect themselves with the `current_tenant` / `require_role` FastAPI dependencies — the tenant claim is **re-verified against `auth_tenant_users` on every request** so a revoked membership takes effect immediately.

### Crypto (`DYNAMO_CRYPTO_*`)

Connection passwords and other secrets are encrypted at rest via AES-256-GCM with per-record DEK wrapping. Generate a master key once:

```bash
python -c "from backend.crypto.envelope import generate_master_key; print(generate_master_key())"
```

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_CRYPTO_MASTER_KEY` | _(empty)_ | Base64-encoded 32 bytes. **Required for admin connection features.** |
| `DYNAMO_CRYPTO_KEY_VERSION` | `1` | Bump when rotating to a new master key |

### Tenant registry cache (`DYNAMO_TENANT_*`)

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_TENANT_REGISTRY_CACHE_SIZE` | `64` | Max `TenantRegistryView` instances kept in memory (LRU by `tenant_id`) |

The cache is strictly bounded — eviction is least-recently-accessed, verified under 500-tenant churn in `tests/test_tenant_registry_cache.py`.

## Auth endpoints (`/api/v1/auth`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/signup` | Email + password signup. Creates a personal tenant with the user as owner. |
| `POST` | `/auth/login` | Email + password login. Returns a new access token. |
| `POST` | `/auth/google` | Verify a Google ID token → existing or new user + token. |
| `GET` | `/auth/me` | Current user, active tenant, all memberships. |

## Admin portal endpoints

All admin endpoints require an authenticated `owner` or `admin` role on the calling tenant — enforced via `require_role("owner", "admin")`. Reads on `/admin/registry/*` are also open to `member`.

### Database connections (`/api/v1/admin/connections`)

```
GET    /admin/connections              List this tenant's connections
POST   /admin/connections              Register a new connection (password encrypted before insert)
GET    /admin/connections/{id}
PATCH  /admin/connections/{id}
DELETE /admin/connections/{id}
POST   /admin/connections/{id}/test    Run the adapter-specific connectivity tester
```

Response DTOs **never** include plaintext passwords — only `has_password: bool`. Plaintext is materialised once inside the service layer and passed straight to the adapter tester — it never crosses a request handler boundary.

### Scaffold jobs (`/api/v1/admin/scaffold-jobs`)

```
POST /admin/connections/{id}/scaffold   Queue a schema inspection (runs in BackgroundTasks)
GET  /admin/scaffold-jobs                List jobs for the calling tenant
GET  /admin/scaffold-jobs/{job_id}       Poll status + progress + result summary
```

Adapter-specific scaffolders register via `ScaffoldService.register_scaffolder(kind, ...)`. The DynamoDB scaffolder ships in Phase 5; new kinds drop in without touching the orchestration.

### Tenant YAML registry (`/api/v1/admin/registry`)

```
GET    /admin/registry/types              Supported resource types (skill / enum / pattern / widget)
GET    /admin/registry/{type}              List entries for the calling tenant
GET    /admin/registry/{type}/{name}       Read YAML source + parsed JSON
PUT    /admin/registry/{type}/{name}       Upsert — parses YAML, computes checksum, invalidates LRU
DELETE /admin/registry/{type}/{name}       Delete + invalidate LRU
```

Stored in `tenant_skills`, `tenant_enums`, `tenant_patterns`, `tenant_widgets`. YAML is parsed once on write and the parsed JSON lives in `parsed_json` so runtime lookups never pay re-parsing cost.

## Cloud adapters (Phase 5)

DynamoUI ships lazy-imported cloud adapters for:

| Kind | SDK | `pip install` extra |
|---|---|---|
| `dynamodb` | `boto3` | `dynamoui[dynamodb]` |
| `spanner` | `google-cloud-spanner` | `dynamoui[spanner]` |
| `oracle` | `oracledb` | `dynamoui[oracle]` |
| `cosmosdb` | `azure-cosmos` | `dynamoui[cosmosdb]` |
| `bigquery` | `google-cloud-bigquery` | `dynamoui[bigquery]` |
| `redshift` | `redshift-connector` | `dynamoui[redshift]` |
| `snowflake` | `snowflake-connector-python` | `dynamoui[snowflake]` |

Each adapter registers a `ConnectionTester` (used by the admin `test` endpoint) and optionally a `Scaffolder`. Wiring lives in [`backend/adapters/cloud_registry.py`](backend/adapters/cloud_registry.py) — adding a new kind only touches that file.

Phase 5 ships the **connection-test** and **scaffold** paths; full query / mutation execution stubs raise `NotImplementedError` so callers can't get silent empty results. Install only the extras you actually use:

```bash
pip install -e ".[dev,dynamodb,spanner]"
```

## Configuring the LLM provider

DynamoUI uses an LLM for two things: synthesising queries at runtime when the pattern cache misses, and seeding cross-entity patterns during scaffold. Both are optional — the app starts and serves pattern-cache hits normally if no API key is set.

Add these to your `.env`:

```env
# Provider — anthropic (default) or google
DYNAMO_LLM_PROVIDER=anthropic
DYNAMO_LLM_ANTHROPIC_API_KEY=sk-ant-...

# Google Gemini (alternative)
# DYNAMO_LLM_PROVIDER=google
# DYNAMO_LLM_GOOGLE_API_KEY=...

# Limits (defaults shown)
DYNAMO_LLM_MAX_TOKENS=4096
DYNAMO_LLM_TIMEOUT_SECONDS=60
```

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_LLM_PROVIDER` | `anthropic` | `anthropic` or `google` |
| `DYNAMO_LLM_ANTHROPIC_API_KEY` | _(empty)_ | Anthropic API key |
| `DYNAMO_LLM_ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Model ID |
| `DYNAMO_LLM_GOOGLE_API_KEY` | _(empty)_ | Google Gemini API key |
| `DYNAMO_LLM_GOOGLE_MODEL` | `gemini-1.5-flash` | Model ID |
| `DYNAMO_LLM_MAX_TOKENS` | `4096` | Max response tokens |
| `DYNAMO_LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout |
| `DYNAMO_LLM_AUTO_PROMOTE_THRESHOLD` | `0.95` | Confidence above which LLM-synthesised patterns are auto-written to YAML |
| `DYNAMO_LLM_REVIEW_QUEUE_THRESHOLD` | `0.90` | Confidence above which patterns are queued for review |
| `DYNAMO_LLM_REVIEW_QUEUE_PATH` | `./pattern_reviews/` | Directory for review-queue YAML files |
| `DYNAMO_LLM_AUTO_PROMOTE_ENABLED` | `true` | Write high-confidence patterns back to YAML automatically |

Missing API key → a warning is logged and LLM features degrade gracefully. The pattern cache continues to serve hits normally.

## Scaffolding a schema

Once your connection is configured, use `dynamoui scaffold` to generate skill YAML from your existing PostgreSQL tables.

### Scaffold a single table

```bash
dynamoui scaffold --adapter postgresql --table employees --output ./skills/employee.skill.yaml
```

### Scaffold all tables in a schema

```bash
dynamoui scaffold --adapter postgresql --schema public --output-dir ./skills/
```

Reflects every table in the `public` schema and writes `<table>.skill.yaml` + `<table>.patterns.yaml` per table, plus a combined `widgets.yaml`. Each file is annotated with `# TODO:` markers for fields that need manual review (sensitive columns, enum refs, display config).

### Scaffold with LLM pattern seeding

Pass `--seed-patterns` to have the LLM generate cross-entity patterns (joins, aggregations, ranking queries) on top of the basic heuristic patterns:

```bash
dynamoui scaffold \
  --adapter postgresql \
  --schema public \
  --output-dir ./skills/ \
  --seed-patterns
```

DynamoUI batches entities into groups of 5 and makes one LLM call per batch, so scaffolding 11 tables costs **3 LLM calls** instead of 11. You can tune the batch size:

```bash
# Smaller batches if responses are still getting truncated
dynamoui scaffold \
  --adapter postgresql \
  --schema public \
  --output-dir ./skills/ \
  --seed-patterns \
  --llm-batch-size 3
```

Confirm seeding worked by checking the logs for:

```
scaffolder.llm_batch_complete   batch=1  entities=["Album","Artist","Customer","Employee","Genre"]
scaffolder.llm_patterns_merged  entity=Album   count=4
scaffolder.llm_patterns_merged  entity=Artist  count=2
...
```

And in the generated `.patterns.yaml` files, LLM-generated patterns have ids prefixed with `<entity_lower>.llm_` and contain `joins`, `aggregations`, or `group_by` fields that the heuristic generator cannot produce.

### Single-table seeding

```bash
dynamoui scaffold \
  --adapter postgresql \
  --table InvoiceLine \
  --output ./skills/invoice_line.skill.yaml \
  --seed-patterns
```

One LLM call for the single table. The full schema context is still sent so the model can generate cross-entity patterns.

### Preview without writing

```bash
dynamoui scaffold --adapter postgresql --schema public --output-dir ./skills/ --dry-run
```

## After scaffolding

Recompute skill hashes and validate before starting the server:

```bash
dynamoui compile-patterns --skills-dir ./skills/
dynamoui validate --skills-dir ./skills/ --enums-dir ./enums/
```

Both must exit 0. The server refuses to start on validation errors.

## Runtime LLM query synthesis

When a user query misses the pattern cache (confidence < 0.80), DynamoUI calls the LLM to synthesise a `QueryPlan` on the fly and executes it immediately. The response includes `source: "llm_synthesis"`.

High-confidence synthesised plans are automatically promoted back to `*.patterns.yaml` (fire-and-forget, does not add latency). Lower-confidence plans are written to `./pattern_reviews/` for operator review.

```
POST /api/v1/resolve
{"input": "top 5 albums by total revenue"}

→ {"intent": "READ", "entity": "Album", "confidence": 0.93,
   "source": "llm_synthesis", "query_plan": {"rows": [...], "total_count": 5}}
```

Cache hits always return `source: "pattern_cache"` and never call the LLM.

## CLI reference

```
dynamoui setup              Interactive PostgreSQL onboarding — writes .env
dynamoui validate           Run the 4-phase validation pipeline against skill files
dynamoui scaffold           Generate skill YAML from a live PostgreSQL table or schema
dynamoui compile-patterns   Recompute skill_hash headers in all *.patterns.yaml files
```

### `dynamoui scaffold` flags

| Flag | Default | Description |
|---|---|---|
| `--adapter` | _(required)_ | Adapter key from `adapters.registry.yaml` |
| `--table` | — | Single table to scaffold |
| `--schema` | `public` | PostgreSQL schema name |
| `--output` | — | Output file path (single-table mode) |
| `--output-dir` | — | Output directory (schema mode) |
| `--dry-run` | `false` | Print YAML without writing to disk |
| `--seed-patterns` | `false` | Use LLM to generate cross-entity patterns |
| `--llm-batch-size` | `5` | Entities per LLM call (only with `--seed-patterns`) |

### `dynamoui validate` flags

```bash
# Validate all skill files
dynamoui validate

# Validate with a live DB schema check (Phase 4)
dynamoui validate --check-connectivity

# Validate a single file
dynamoui validate --file ./skills/employee.skill.yaml

# JSON output (useful for CI)
dynamoui validate --output json
```

### Starting the server

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
# or
python -m backend.main
```

## LLM Metering

DynamoUI records every LLM call and the operation that triggered it. Metering data is written to a dedicated internal PostgreSQL schema (`dynamoui_internal`) that is completely separate from your application data.

### What is tracked

**Every operation** that may trigger an LLM call gets one row in `metering_operations`:

| Operation type | When it fires |
|---|---|
| `resolve` | Each `POST /api/v1/resolve` call |
| `scaffold_table` | Each `dynamoui scaffold --table` run (when `--seed-patterns` is set) |
| `scaffold_schema` | Each LLM batch within `dynamoui scaffold --schema` (when `--seed-patterns` is set) |

**Each actual LLM API call** within that operation gets one row in `metering_llm_interactions`, capturing:

- Provider and model name
- Prompt, completion, and thinking token counts
- Cost in USD (calculated at write time using the active rate from `metering_cost_rates`)
- Latency in milliseconds
- First 500 characters of any thinking block (for auditors optimising prompts)
- FK to the exact cost rate row used — historical costs are preserved even when rates change

### Internal schema setup

All DynamoUI-owned tables live in the `dynamoui_internal` PostgreSQL schema — metering, auth, tenant DB connections, scaffold jobs, and the tenant YAML registry. Create everything in one shot:

```bash
# From the repo root
alembic upgrade head
```

| Revision | Tables |
|---|---|
| `001_metering` | `metering_cost_rates`, `metering_operations`, `metering_llm_interactions` |
| `002_auth` | `auth_tenants`, `auth_users`, `auth_tenant_users`, `auth_oauth_identities` |
| `003_tenant_connections` | `tenant_db_connections` (encrypted secrets) |
| `004_scaffold_jobs` | `tenant_scaffold_jobs` |
| `005_tenant_registry` | `tenant_skills`, `tenant_enums`, `tenant_patterns`, `tenant_widgets` |

Per-phase rollback instructions live in [`docs/RELEASE_NOTES_MULTI_TENANT.md`](docs/RELEASE_NOTES_MULTI_TENANT.md). Every migration ships an idempotent `downgrade()`; always take a fresh logical backup of `dynamoui_internal` before rolling back across the auth chain because the FKs cascade.

The initial metering migration also seeds cost rates for the configured default providers.

> **Permissions required**: the write user (`dynamoui_writer`) must be able to create schemas and tables. The migration grants the necessary permissions automatically. If you are using a restricted write user, run the migration as a superuser or database owner once, then revoke the CREATE privilege afterwards.

To check the current migration status:

```bash
alembic current
alembic history
```

To roll back (drops all metering tables and the schema):

```bash
alembic downgrade base
```

### Configuration

Metering is controlled by `DYNAMO_INTERNAL_*` environment variables. Add them to your `.env`:

```env
# PostgreSQL schema for all DynamoUI-managed tables (default shown)
DYNAMO_INTERNAL_DB_SCHEMA=dynamoui_internal

# Optional: separate DB URL for the internal schema.
# Defaults to the write pool URL (same database, different schema).
# DYNAMO_INTERNAL_DB_URL=postgresql+asyncpg://user:pass@host:5432/db
```

| Variable | Default | Description |
|---|---|---|
| `DYNAMO_INTERNAL_DB_SCHEMA` | `dynamoui_internal` | PostgreSQL schema name for metering tables |
| `DYNAMO_INTERNAL_DB_URL` | _(uses write pool URL)_ | Override to point metering at a separate database |

Metering is **best-effort** — if the metering database is unavailable at startup, the service starts in no-op mode (one warning logged, no further errors). If a write fails at runtime, it is logged at `WARN` level and swallowed; the original request is never affected.

### Metering API

Once the migration has run, the metering endpoints are available under `/api/v1/metering`:

```
GET  /api/v1/metering/summary               Totals: operations, cache hit rate, tokens, cost
GET  /api/v1/metering/operations            Paginated operation list (?operation_type=resolve)
GET  /api/v1/metering/operations/{id}       Single operation + its LLM interactions
GET  /api/v1/metering/cost-by-model         Cost aggregated by provider + model
GET  /api/v1/metering/cost-rates            Full history of cost rates (append-only ledger)
POST /api/v1/metering/cost-rates            Add a new cost rate version
```

### Managing cost rates

The `metering_cost_rates` table is an append-only ledger — rows are never updated or deleted. Each price change creates a new row and closes the previous one. Every interaction row stores a FK to the exact rate row used, so historical costs are never retroactively altered.

To add a new rate when Anthropic or Google changes their pricing:

```bash
curl -X POST http://localhost:8001/api/v1/metering/cost-rates \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "input_cost_per_1k": "0.00080000",
    "output_cost_per_1k": "0.00400000",
    "thinking_cost_per_1k": "0.00080000",
    "effective_from": "2026-05-01",
    "change_reason": "Anthropic May 2026 pricing update",
    "source_reference": "https://www.anthropic.com/pricing",
    "created_by": "ops-team"
  }'
```

`change_reason` and `created_by` are required and must be non-blank — the DAO enforces this as an audit requirement.

### Schema DDL

The canonical schema definition lives in `backend/metering/models/tables.py` (SQLAlchemy Core). A human-readable DDL file is derived from it:

```bash
python -m backend.metering.schema.export
# Writes: backend/metering/schema/metering_schema.sql
```

Commit `metering_schema.sql` alongside any changes to `tables.py`. CI can detect drift by running the export and checking `git diff`.

### Multi-tenancy

Multi-tenancy shipped across Phases 1–6 (see [`docs/MULTI_TENANT_PLAN.md`](docs/MULTI_TENANT_PLAN.md)):

* Every sign-up creates a personal tenant with the user as `owner`. The N:M `auth_tenant_users` table supports additional members in the same tenant (the frontend switcher hook is trivial to add).
* `tenant_id` is carried in the JWT `tid` claim and re-verified against `auth_tenant_users` on every request.
* Every tenant-scoped DAO takes `tenant_id` as an explicit argument — cross-tenant access is impossible by construction and covered by unit tests in `test_tenant_connections.py`, `test_tenant_scaffold.py`, and `test_tenant_registry_service.py`.
* DB connection credentials are AES-256-GCM envelope-encrypted via `backend/crypto/envelope.py`. Plaintext never reaches the database or any response DTO.
* Tenant YAML configs are stored in `tenant_{skills,enums,patterns,widgets}` and served at runtime via a bounded LRU cache (`DYNAMO_TENANT_REGISTRY_CACHE_SIZE`, default 64).
* The metering tables already had `tenant_id` columns — they now receive the real tenant id from the auth subsystem rather than NULL.

---

## Architecture

```
backend/
  auth/                   Phase 1 — tenants, users, JWT, Google OAuth
    models/tables.py      auth_tenants, auth_users, auth_tenant_users, auth_oauth_identities
    security.py           scrypt password hashing + python-jose JWT helpers
    dao.py                AuthDAO — tenant-segregated lookups + signup transaction
    service.py            AuthService — signup / login / google_login (injectable verifier)
    api/routes.py         /api/v1/auth/{signup,login,google,me}
    api/dependencies.py   get_current_user / get_current_tenant / require_role
  crypto/                 Phase 2 — AES-256-GCM envelope with per-record DEK wrapping
    envelope.py           encrypt() / decrypt() — ONLY place cryptography.hazmat is imported
  tenants/
    connections/          Phase 2 — tenant-scoped DB connection registry (encrypted secrets)
      tables.py           tenant_db_connections
      service.py          ConnectionService — tester registry, materialise() for adapters
      routes.py           /api/v1/admin/connections/*
    scaffold/             Phase 3 — async schema-inspection jobs
      tables.py           tenant_scaffold_jobs
      service.py          ScaffoldService — Scaffolder protocol + BackgroundTasks runner
      routes.py           /api/v1/admin/connections/{id}/scaffold + /admin/scaffold-jobs
    registry/             Phase 4 — tenant YAML registry + bounded LRU runtime cache
      tables.py           tenant_skills, tenant_enums, tenant_patterns, tenant_widgets
      cache.py            TenantRegistryCache — strict LRU keyed on tenant_id
      service.py          RegistryService — parse-once-on-write, invalidate on mutation
      routes.py           /api/v1/admin/registry/*
  adapters/               SQLAlchemy adapter layer + cloud adapters (Phase 5)
    postgresql/           QueryTranslator — joins, aggregations, TOP N
    cloud_base.py         CloudDataAdapter + lazy_import helper
    cloud_registry.py     register_cloud_adapters() — single place to wire new kinds
    kinds.py              Canonical adapter-kind identifiers (POSTGRESQL, DYNAMODB, …)
    dynamodb/             boto3 — tester + scaffolder
    spanner/              google-cloud-spanner — tester
    oracle/               oracledb — tester
    cosmosdb/             azure-cosmos — tester
  metering/               LLM usage metering subsystem
    models/               SQLAlchemy Core table definitions (canonical SDL)
    dto/                  Pydantic DTOs — Create / Update / Read per entity
    dao/                  Data Access Objects — all SQL lives here
    api/                  GET /metering/* + POST /metering/cost-rates
    schema/               DDL export script + committed metering_schema.sql
    context.py            MeteringContext ContextVar (threading-free call chain)
    cost.py               CostCalculator — Decimal-safe USD cost from token counts
    provider_decorator.py MeteringLLMProvider — wraps any LLMProvider automatically
    service.py            MeteringService — high-level API, all exceptions swallowed
  pattern_cache/          Fuzzy trigger matching, pattern caching, pattern promotion
    promotion/            PatternPromoter — auto-write or review-queue LLM patterns
  skill_registry/         Skill/enum YAML loading, validation, LLM formatting
    llm/                  LLM provider abstraction, QuerySynthesiser, PatternSeeder
    cli/                  Click CLI entry points
    config/               Pydantic Settings (DYNAMO_PG_*, DYNAMO_SKILL_*, DYNAMO_CACHE_*,
                          DYNAMO_LLM_*, DYNAMO_INTERNAL_*, DYNAMO_AUTH_*, DYNAMO_CRYPTO_*,
                          DYNAMO_TENANT_*)
skills/                   *.skill.yaml + *.patterns.yaml — platform default definitions
                          (tenant-specific YAML lives in the tenant_* tables)
enums/                    *.enum.yaml — platform default enums
pattern_reviews/          Candidate patterns awaiting operator review
alembic/                  Database migrations (internal schema only)
  versions/               001_metering → 002_auth → 003_tenant_connections →
                          004_scaffold_jobs → 005_tenant_registry
docs/                     MULTI_TENANT_PLAN.md, RELEASE_NOTES_MULTI_TENANT.md
tests/                    pytest test suite — in-memory fakes for auth / crypto /
                          connections / scaffold / registry / cloud adapters
```

## License

MIT — see [LICENSE](LICENSE).
