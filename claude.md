# CLAUDE.md — DynamoUI

> Authoritative guide for Claude Code. Read fully before making any change.
> Every rule here is derived directly from the LLD documents — do not deviate without flagging it.
> Multi-tenant rollout status + phase-by-phase handoffs live in [`docs/MULTI_TENANT_PLAN.md`](docs/MULTI_TENANT_PLAN.md). Release notes + rollback runbook live in [`docs/RELEASE_NOTES_MULTI_TENANT.md`](docs/RELEASE_NOTES_MULTI_TENANT.md).

---

## Project Overview

**DynamoUI** is an Adaptive Data Interface Framework — a generic, LLM-powered system that generates interactive UIs for backend data models on demand. Teams onboard any data model by writing a skill YAML file and immediately get a fully functional, natural-language-driven UI with zero frontend code.

DynamoUI is **multi-tenant**: every sign-up creates a personal tenant, credentials are AES-GCM encrypted at rest, and runtime YAML configs are served per-tenant via a bounded LRU cache.

**Current state:** Phase 6 of the multi-tenant rollout shipped. All six phases (auth, connection registry + encryption, admin portal + scaffold jobs, tenant YAML registry + LRU cache, cloud adapters, release hardening) are on `main`.

---

## Architecture: Single Deployable Monolith

One backend service + one frontend application. No microservices, no gRPC between modules. All internal module calls are direct in-process Python function calls.

```
backend/
  auth/               # Phase 1 — tenants, users, JWT, Google OAuth
  crypto/             # Phase 2 — AES-GCM envelope encryption (single source of hazmat)
  tenants/
    connections/      # Phase 2 — tenant-scoped DB connection registry
    scaffold/         # Phase 3 — async scaffold jobs
    registry/         # Phase 4 — tenant YAML registry + bounded LRU runtime cache
  skill_registry/     # LLD 1, 2 — Skill + Enum registry, loader, validator, CLI
  pattern_cache/      # LLD 4 — Pattern cache, fuzzy matcher, trigger index
  adapters/           # LLD 3 — DataAdapter ABC, AdapterRegistry, PostgreSQLAdapter
    cloud_base.py     # Phase 5 — CloudDataAdapter + lazy_import helper
    cloud_registry.py # Phase 5 — register_cloud_adapters(...) called at startup
    dynamodb/         # Phase 5 — boto3 tester + scaffolder
    spanner/          # Phase 5 — google-cloud-spanner tester
    oracle/           # Phase 5 — oracledb tester
    cosmosdb/         # Phase 5 — azure-cosmos tester
  metering/           # LLM usage metering subsystem
frontend/
  src/
    auth/             # Phase 1 — AuthContext, tokenStorage
    components/auth/  # Phase 1 — AuthScreen, ProtectedRoute
    admin/            # Phase 3/4 — AdminPortal, Connections, Scaffold, Registry pages
    components/       # LLD 6, 7, 8 — DataTable, DetailCard, Dashboard, theming
    lib/
      apiClient.ts    # Shared typed API client — the ONLY place fetch() is called
skills/               # *.skill.yaml, *.enum.yaml, *.patterns.yaml, *.mutations.yaml
                      # Phase 4: tenant-owned YAML lives in tenant_* DB tables, not on disk.
                      # Platform defaults still live here and are merged per-tenant at lookup.
widgets.yaml          # Widget dashboard definitions (LLD 8)
adapters.registry.yaml
docs/                 # MULTI_TENANT_PLAN.md + RELEASE_NOTES_MULTI_TENANT.md
alembic/versions/     # 001_metering → 002_auth → 003_tenant_connections →
                      # 004_scaffold_jobs → 005_tenant_registry
```

> **Rule:** Never propose splitting into separate services. Flag any boundary-crossing refactor before proceeding.

---

## Tech Stack

### Backend
- **Language:** Python 3.11+
- **Framework:** FastAPI
- **Validation:** Pydantic v2 — use `model_validator(mode='after')`, `field_validator`, `SettingsConfigDict`. Never use Pydantic v1 `@validator` or `class Config`.
- **DB access:** SQLAlchemy 2.0 async Core (`sqlalchemy.ext.asyncio`) + `asyncpg`. **Not ORM.** DynamoUI reflects tables it does not own.
- **Database:** PostgreSQL for the internal schema (metering, auth, tenant registry). Cloud adapters (Phase 5): DynamoDB, Spanner, Oracle, Cosmos DB — all lazy-imported via `backend/adapters/cloud_base.lazy_import`.
- **Auth:** `python-jose` JWTs + stdlib `hashlib.scrypt` for password hashing. **No `passlib`, no `bcrypt` dependency.** JWT claims: `sub` (user id), `tid` (tenant id), `email`, `role`, `iat`, `exp`. Use `get_current_tenant` / `require_role` dependencies — **never** read `tenant_id` from query strings or headers.
- **Encryption:** AES-256-GCM envelope with per-record DEK wrapping via `backend/crypto/envelope.py`. `cryptography.hazmat` is imported **only** in that module — future phases must reuse `encrypt()` / `decrypt()` rather than re-importing.
- **Fuzzy matching:** RapidFuzz `fuzz.token_sort_ratio` via `process.extractOne`. RapidFuzz scores are **0–100**, not 0–1. Divide by 100 when storing as `confidence`. Default hit threshold: **0.90** (i.e., `score_cutoff=90`).
- **Pattern cache:** Platform default YAMLs on disk; tenant-owned YAMLs in `tenant_patterns` served via `TenantRegistryCache`.
- **Logging:** `structlog`, JSON to stdout
- **CLI:** Click (`dynamoui validate`, `dynamoui scaffold`, `dynamoui compile-patterns`)
- **Settings:** Pydantic Settings with env prefixes (see below)

### Frontend
- **Framework:** React 18 + TypeScript
- **Build tool:** Vite
- **Tables:** TanStack Table v8 (headless) — `useReactTable`, `getCoreRowModel`. Manual sorting/filtering/pagination (`manualSorting: true`, etc.)
- **Data fetching:** React Query (TanStack Query) — all server state. Mutations via `useMutation`.
- **Styling:** Tailwind CSS + CSS Custom Properties (`--dui-*` prefix)
- **Icons:** Lucide React
- **API:** `src/lib/apiClient.ts` — **no component ever calls `fetch()` directly**

---

## Full Repository Structure

```
backend/
  skill_registry/
  ├── models/
  │   ├── skill.py          # EntitySkill, FieldDef, DisplayConfig, NotificationConfig
  │   ├── enum.py           # EnumSkill, EnumValue
  │   ├── pattern.py        # PatternFile, Pattern, PatternParam
  │   ├── mutation.py       # MutationFile, Mutation, ValidationRule
  │   └── registry.py       # AdapterRegistry, SkillRegistry (in-memory index)
  ├── loader/
  │   ├── yaml_loader.py    # YAML file discovery + parsing
  │   └── validator.py      # Cross-file validation (FK resolution, enum refs)
  ├── registry/
  │   └── enum_registry.py  # EnumRegistry in-memory index + query methods
  ├── formatters/
  │   ├── llm_formatter.py  # Plain text for LLM prompts (NOT JSON — intentional)
  │   └── ui_formatter.py   # JSON for UI dropdowns
  ├── scaffold/
  │   └── scaffolder.py     # DB schema -> skill YAML generation
  ├── cli/
  │   └── validate.py       # Click CLI (validate + scaffold + compile-patterns)
  ├── api/
  │   └── rest_router.py    # FastAPI router — single service
  └── config/
      └── settings.py       # Pydantic Settings

  pattern_cache/            # Sibling of skill_registry — NOT nested inside it
  ├── models/
  │   └── pattern.py        # Pattern, PatternParam, MatchResult, CacheLookupResult
  ├── loader/
  │   └── pattern_loader.py # YAML discovery + Pydantic validation
  ├── index/
  │   ├── trigger_index.py  # Flat index: normalised_trigger -> pattern_id
  │   └── fuzzy_matcher.py  # RapidFuzz matching engine
  ├── cache/
  │   └── pattern_cache.py  # PatternCache facade — lookup(), stats
  ├── promotion/
  │   └── promoter.py       # PatternPromoter — Phase 2 stub (pass body only)
  ├── versioning/
  │   └── hasher.py         # PatternHasher — SHA-256 skill YAML hash
  └── api/
      └── rest_router.py    # /patterns REST endpoints

  adapters/                 # Sibling of skill_registry — NOT nested inside it
  ├── base.py               # DataAdapter ABC + data classes (QueryPlan, MutationPlan, etc.)
  ├── registry.py           # AdapterRegistry — reads adapters.registry.yaml at startup
  └── postgresql/
      ├── adapter.py        # PostgreSQLAdapter(DataAdapter)
      ├── engine.py         # Async engine + pool management (PostgreSQLEngine)
      ├── table_builder.py  # Skill YAML -> sa.Table (TableBuilder)
      ├── query_translator.py  # QueryPlan -> sa.select() (QueryTranslator)
      ├── mutation_executor.py # MutationPlan -> insert/update/delete in transaction
      ├── diff_builder.py   # DiffPreview generation (in-memory, no DB write)
      ├── schema_validator.py  # Validate skill YAML against live DB (--check-connectivity)
      ├── schema_inspector.py  # Inspect live DB -> skill YAML scaffold
      └── type_map.py       # Skill field types <-> SQLAlchemy column types

frontend/
  src/
  ├── components/
  │   ├── dashboard/
  │   │   ├── Dashboard.tsx
  │   │   ├── WidgetGrid.tsx
  │   │   ├── WidgetCard.tsx
  │   │   ├── WidgetParamModal.tsx
  │   │   ├── CategoryHeader.tsx
  │   │   └── useDashboard.ts
  │   └── data-display/
  │       ├── DataTable/
  │       │   ├── DataTable.tsx
  │       │   ├── DataTableHeader.tsx
  │       │   ├── DataTableRow.tsx
  │       │   ├── DataTableCell.tsx
  │       │   ├── DataTablePagination.tsx
  │       │   ├── DataTableFilter.tsx
  │       │   ├── DataTableEmpty.tsx
  │       │   └── useDataTable.ts
  │       ├── DetailCard/
  │       │   ├── DetailCard.tsx
  │       │   ├── DetailCardField.tsx
  │       │   ├── DetailCardRelations.tsx
  │       │   └── DetailCardActions.tsx
  │       ├── CellRenderers/
  │       │   ├── TextCell.tsx
  │       │   ├── NumberCell.tsx
  │       │   ├── BooleanCell.tsx
  │       │   ├── DateCell.tsx
  │       │   ├── EnumCell.tsx
  │       │   ├── UUIDCell.tsx
  │       │   ├── FKCell.tsx
  │       │   └── SensitiveCell.tsx
  │       └── InlineEdit/
  │           ├── EditableCell.tsx       # Double-click-to-edit wrapper
  │           ├── InlineDiffPreview.tsx  # Compact diff confirm panel
  │           └── useInlineEdit.ts       # Mutation preview/execute hook
  └── lib/
      └── apiClient.ts

tests/
  fixtures/
    skills/     # Valid + invalid *.skill.yaml — reuse across test suites
    enums/      # Valid + invalid *.enum.yaml
    patterns/   # Valid + edge-case *.patterns.yaml
    themes/     # theme-default.css, theme-high-contrast.css, theme-incomplete.css
```

---

## Environment Variables

### `DYNAMO_AUTH_*` — Auth subsystem (Phase 1)

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_AUTH_JWT_SECRET` | dev placeholder | HS256 signing secret. **Required in prod.** |
| `DYNAMO_AUTH_JWT_ALGORITHM` | `HS256` | |
| `DYNAMO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | `3600` | Access token lifetime |
| `DYNAMO_AUTH_SIGNUP_ENABLED` | `true` | Disable to shut off public signups |
| `DYNAMO_AUTH_GOOGLE_CLIENT_ID` | — | Empty disables Google login |
| `DYNAMO_AUTH_GOOGLE_TOKENINFO_URL` | `https://oauth2.googleapis.com/tokeninfo` | Overridden in tests |
| `DYNAMO_AUTH_SCRYPT_N` | `16384` | Password hashing CPU/memory cost |
| `DYNAMO_AUTH_SCRYPT_R` | `8` | Block size |
| `DYNAMO_AUTH_SCRYPT_P` | `1` | Parallelisation |

### `DYNAMO_CRYPTO_*` — Envelope encryption (Phase 2)

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_CRYPTO_MASTER_KEY` | — | Base64-encoded 32 bytes. **Required** for admin connection features. Generate via `python -c "from backend.crypto.envelope import generate_master_key; print(generate_master_key())"`. |
| `DYNAMO_CRYPTO_KEY_VERSION` | `1` | Bump on rotation; older versions stay decryptable via the envelope `v` field. |

### `DYNAMO_TENANT_*` — Tenant registry LRU cache (Phase 4)

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_TENANT_REGISTRY_CACHE_SIZE` | `64` | Strict LRU bound on `TenantRegistryView` instances. Eviction is by least-recently-accessed. Tested under 500-tenant churn. |

### `DYNAMO_SKILL_*` — Skill Registry

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_SKILL_SKILLS_DIR` | `./skills` | `*.skill.yaml` discovery root |
| `DYNAMO_SKILL_ENUMS_DIR` | `./enums` | `*.enum.yaml` discovery root |
| `DYNAMO_SKILL_ADAPTERS_REGISTRY` | `./adapters.registry.yaml` | |
| `DYNAMO_SKILL_REST_PORT` | `8001` | FastAPI port |
| `DYNAMO_SKILL_JWT_SECRET` | — | **Required in prod. Never hardcode.** |
| `DYNAMO_SKILL_LOG_LEVEL` | `INFO` | `INFO` or `DEBUG` |
| `DYNAMO_SKILL_LOG_FORMAT` | `json` | `json` or `console` |
| `DYNAMO_SKILL_ENABLE_SLACK_NOTIFICATIONS` | `false` | Feature-flagged off. v2 only. |
| `DYNAMO_SKILL_ENABLE_WEBHOOK_NOTIFICATIONS` | `false` | Feature-flagged off. v2 only. |
| `DYNAMO_SKILL_FUZZY_MATCH_SHADOW_THRESHOLD` | `0.85` | Shadowed pattern detection in validation |

### `DYNAMO_PG_*` — PostgreSQL Adapter

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_PG_HOST` | `localhost` | |
| `DYNAMO_PG_PORT` | `5432` | |
| `DYNAMO_PG_DATABASE` | `dynamoui` | |
| `DYNAMO_PG_USER` | `dynamoui_reader` | Read-only user for all queries |
| `DYNAMO_PG_PASSWORD` | — | **Never hardcode. Use `SecretStr`.** |
| `DYNAMO_PG_WRITE_USER` | `dynamoui_writer` | Mutations only |
| `DYNAMO_PG_WRITE_PASSWORD` | — | **Never hardcode.** |
| `DYNAMO_PG_POOL_SIZE` | `10` | |
| `DYNAMO_PG_MAX_OVERFLOW` | `20` | |
| `DYNAMO_PG_POOL_TIMEOUT` | `30` | seconds |
| `DYNAMO_PG_POOL_RECYCLE` | `3600` | seconds |
| `DYNAMO_PG_ECHO_SQL` | `false` | Dev only |
| `DYNAMO_PG_SSL_MODE` | `prefer` | Use `require` in production |

Connection URL property:
```python
f'postgresql+asyncpg://{user}:{password.get_secret_value()}@{host}:{port}/{database}'
```

### `DYNAMO_CACHE_*` — Pattern Cache

| Variable | Default | Notes |
|---|---|---|
| `DYNAMO_CACHE_FUZZY_THRESHOLD` | `0.90` | Minimum score for a cache hit (0.0–1.0) |
| `DYNAMO_CACHE_FUZZY_SCORER` | `token_sort_ratio` | RapidFuzz scorer |
| `DYNAMO_CACHE_ENTITY_SCOPED_MATCHING` | `true` | Prefer entity-scoped trigger lookup |
| `DYNAMO_CACHE_STOPWORDS` | see below | Stripped before fuzzy match |
| `DYNAMO_CACHE_AUTO_PROMOTE_ENABLED` | `false` | **Phase 2 stub — keep false in Phase 1** |
| `DYNAMO_CACHE_ENFORCE_SKILL_HASH` | `true` | Reject patterns with stale hashes |
| `DYNAMO_CACHE_HASH_LENGTH` | `16` | Truncated SHA-256 prefix |
| `DYNAMO_CACHE_STATS_LOG_INTERVAL_SECONDS` | `300` | Hit rate log interval |

Default stopwords list (stripped before matching):
```python
['the', 'a', 'an', 'all', 'show', 'me', 'get', 'find', 'list', 'please', 'can', 'you']
```

### Frontend

| Variable | Default | |
|---|---|---|
| `REACT_APP_API_BASE_URL` | `/api/v1` | Backend base URL |

---

## Startup Sequence

The backend **refuses to start** if validation fails. Never weaken this gate.

1. Load config from environment
2. Discover all `*.skill.yaml`, `*.enum.yaml`, `*.patterns.yaml`, `*.mutations.yaml`
3. Run 4-phase validation pipeline — on any error: log full details, `exit(1)`
4. Build in-memory indexes: `entity_by_name`, `enum_by_name`, `patterns_by_entity`, `fk_graph`
5. Load `widgets.yaml` — missing file is a **warning**, not fatal; dashboard renders empty
6. Start FastAPI on `REST_PORT`
7. Emit: `skill_registry.boot_time_ms`, `skill_registry.entities_loaded`, `skill_registry.patterns_loaded`

---

## Validation Pipeline (4 Phases)

Run via `dynamoui validate` or at startup.

| Phase | Scope | Checks |
|---|---|---|
| **1 — Schema** | Per-file | Pydantic parse, required fields, type formats, PascalCase/snake_case |
| **2 — Cross-Reference** | All files | FK targets exist + are PKs, `enumRef` resolves, adapter keys in registry, pattern file paths on disk, entity name + pattern ID global uniqueness |
| **3 — Semantic** | All files | Circular FK detection, missing `mutations_file` warnings, shadowed triggers >0.85 similarity |
| **4 — Connectivity** | Live DB | Adapter reachability + skill YAML vs live schema column check (only with `--check-connectivity`) |

**CI gate:** `dynamoui validate` runs on every PR touching `skills/`, `enums/`, `models/`. Must pass before merge.

---

## CLI Commands

```bash
# Validate all skill files
dynamoui validate --skills-dir ./skills/ --enums-dir ./enums/ --adapters-registry ./adapters.registry.yaml

# Validate single file
dynamoui validate --file ./skills/employee.skill.yaml

# Validate + live DB connectivity check
dynamoui validate --skills-dir ./skills/ --check-connectivity

# JSON output for CI
dynamoui validate --skills-dir ./skills/ --output json

# Scaffold from existing PostgreSQL table
dynamoui scaffold --adapter postgresql --table employees --output ./skills/employee.skill.yaml

# Scaffold all tables in a schema
dynamoui scaffold --adapter postgresql --schema public --output-dir ./skills/

# Dry run
dynamoui scaffold --adapter postgresql --table employees --dry-run

# Recompute pattern hashes — run in CI on every skill file change
dynamoui compile-patterns --skills-dir ./skills/
```

Scaffold output header: `# Auto-generated by dynamoui scaffold. Review all TODO fields before committing.`

---

## Key Code Patterns

These patterns are locked in by the LLDs. Follow them exactly.

### Pattern Cache — Fuzzy Matching

```python
from rapidfuzz import fuzz, process

class FuzzyMatcher:
    def __init__(self, index: TriggerIndex, threshold: float = 0.9):
        self._threshold = threshold  # stored as 0.0–1.0

    def match(self, user_input: str, entity_hint: str | None = None) -> MatchResult | None:
        normalised = self._index._normalise(user_input)
        entries = (
            self._index._by_entity.get(entity_hint, [])
            if entity_hint else self._index._entries
        )
        choices = [e.trigger_normalised for e in entries]
        result = process.extractOne(
            normalised, choices,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=self._threshold * 100  # RapidFuzz uses 0–100, NOT 0–1
        )
        if result is None:
            return None
        matched_text, score, idx = result
        entry = entries[idx]
        return MatchResult(
            pattern_id=entry.pattern_id,
            confidence=score / 100.0,  # convert back to 0.0–1.0 for storage
            matched_trigger=entry.trigger_original,
            entity=entry.entity
        )
```

### Pattern Cache — Confidence Tiers (Primary LLM-Call Control)

This is the mechanism that avoids LLM calls for the majority of user queries. **Do not change thresholds without an explicit design decision.**

| Score | Action | LLM Call? |
|---|---|---|
| >= 0.95 | Execute pattern directly, no confirmation | **No** |
| 0.90 – 0.94 | Execute pattern directly, log as near-miss for review | **No** |
| 0.80 – 0.89 | Show "Did you mean…?" prompt to user | **No** (unless user rejects) |
| < 0.80 | Cache miss — fall through to LLM (Phase 2) | **Yes** |

### Pattern Versioning

```python
import hashlib
from pathlib import Path

class PatternHasher:
    @staticmethod
    def compute_skill_hash(skill_path: Path) -> str:
        content = skill_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]  # truncated to 16 chars

    @staticmethod
    def verify(pattern_file: Path, skill_path: Path) -> bool:
        # Pattern file header format: # skill_hash: abc123def456789a
        header = pattern_file.read_text().split('\n')[0]
        stored_hash = header.split('skill_hash:')[1].strip()
        current_hash = PatternHasher.compute_skill_hash(skill_path)
        return stored_hash == current_hash
```

Every `*.patterns.yaml` must start with `# skill_hash: <16-char-hash>`. `dynamoui compile-patterns` regenerates these.

### Adapter — Table Builder (Core, not ORM)

```python
import sqlalchemy as sa

class TableBuilder:
    def build(self, skill: EntitySkill) -> sa.Table:
        columns = []
        for field in skill.fields:
            col_type = TYPE_MAP[field.type]  # from type_map.py
            col = sa.Column(
                field.name,
                col_type,
                primary_key=field.isPK,
                nullable=field.nullable if not field.isPK else False,
            )
            columns.append(col)
        return sa.Table(skill.table, self._metadata, *columns)
```

**Critical:** FK joins are resolved at query time using the FK graph from the Skill Registry. **Do NOT use `sa.ForeignKey` constraints** — DynamoUI does not own the tables and cannot guarantee FK constraints exist in the target DB.

### Adapter — QueryTranslator Filter Ops

```python
class QueryTranslator:
    FILTER_OPS = {
        'eq':      lambda c, v: c == v,
        'ne':      lambda c, v: c != v,
        'gt':      lambda c, v: c > v,
        'gte':     lambda c, v: c >= v,
        'lt':      lambda c, v: c < v,
        'lte':     lambda c, v: c <= v,
        'in':      lambda c, v: c.in_(v),
        'like':    lambda c, v: c.ilike(f'%{v}%'),
        'is_null': lambda c, v: c.is_(None) if v else c.isnot(None),
    }
```

To add a new filter operator: add a lambda to `FILTER_OPS`. Never construct SQL strings. Never add raw SQL outside this dict.

### Adapter — Type Map

| Skill Type | SQLAlchemy Type | Notes |
|---|---|---|
| `string` | `sa.String` / `sa.Text` | Text if no max_length |
| `integer` | `sa.Integer` / `sa.BigInteger` | BigInteger for PKs |
| `float` | `sa.Float` / `sa.Numeric` | Numeric for currency |
| `boolean` | `sa.Boolean` | |
| `date` | `sa.Date` / `sa.DateTime` | DateTime if time component |
| `uuid` | `sa.UUID` | Native PostgreSQL UUID |
| `enum` | `sa.String` | Stored as string, validated via EnumRegistry |
| `json` | `sa.JSON` | PostgreSQL JSON (not JSONB in v1) |

### Enum Registry — LLM Format

```python
def format_for_llm(enum_name: str) -> str:
    # Returns PLAIN TEXT, not JSON — intentional.
    # Lower token count than JSON. LLMs process natural language more reliably.
    # Deprecated values explicitly flagged to prevent LLM suggestions in mutations.
    #
    # Example output:
    # Enum: EmploymentType — Classification of employment relationship
    # Valid values:
    #   FULL_TIME (displayed as 'Full Time') — Permanent full-time employee
    #   CONTRACT (displayed as 'Contractor') — Fixed-term contract worker
    #   INTERN (displayed as 'Intern') [DEPRECATED — do not suggest]
```

### EnumDropdown — mode prop

```typescript
// 'create' -> activeOptions only (excludes deprecated)
// 'edit'   -> all options including deprecated (field may already hold deprecated value)
// 'filter' -> all options including deprecated
<EnumDropdown enumName="EmploymentType" value={val} onChange={fn} mode="create" />
```

### Frontend — React Query Key Structure

Use this exact structure. Cache invalidation on successful mutation must invalidate `entityList` + `singleRecord` for the entity.

```typescript
const queryKeys = {
  entityList:    (entity: string, sort?: Sort, page?: number, filters?: Filters) =>
                   ['entityList', entity, sort, page, filters],
  singleRecord:  (entity: string, pk: string) =>
                   ['singleRecord', entity, pk],
  displayConfig: (entity: string) =>
                   ['displayConfig', entity],
  fieldMeta:     (entity: string) =>
                   ['fieldMeta', entity],
  mutationDefs:  (entity: string) =>
                   ['mutationDefs', entity],
  enumOptions:   (enumName: string) =>
                   ['enumOptions', enumName],
} as const;
```

### Frontend — TanStack Table Setup

```typescript
const table = useReactTable({
  data: result.rows,
  columns,
  getCoreRowModel: getCoreRowModel(),
  manualSorting: true,      // server-side — always
  manualFiltering: true,    // server-side — always
  manualPagination: true,   // server-side — always
  rowCount: result.totalCount,
});
```

### Frontend — apiClient (canonical shape)

```typescript
// src/lib/apiClient.ts — the ONLY place fetch() is called
export const apiClient = {
  resolve: (input: string) =>
    apiFetch<ResolutionResult>('/resolve', { method: 'POST', body: JSON.stringify({ input }) }),
  fetchEntityList: (entity: string, params: QueryParams) =>
    apiFetch<QueryResult>(`/entities/${entity}?${toQueryString(params)}`),
  fetchSingleRecord: (entity: string, pk: string) =>
    apiFetch<QueryResult>(`/entities/${entity}/${pk}`),
  fetchDisplayConfig: (entity: string) =>
    apiFetch<DisplayConfig>(`/schema/${entity}/display`),
  fetchFieldMeta: (entity: string) =>
    apiFetch<FieldMeta[]>(`/schema/${entity}/fields`),
  fetchMutationDefs: (entity: string) =>
    apiFetch<MutationDef[]>(`/schema/${entity}/mutations`),
  fetchEnumOptions: (enumName: string) =>
    apiFetch<EnumOptions>(`/enums/${enumName}/options`),
  previewMutation: (plan: MutationPlan) =>
    apiFetch<DiffPreview>('/mutate/preview', { method: 'POST', body: JSON.stringify(plan) }),
  executeMutation: (plan: MutationPlan) =>
    apiFetch<MutationResult>('/mutate/execute', { method: 'POST', body: JSON.stringify(plan) }),
};
```

---

## REST API Reference

### Auth (Phase 1)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/signup` | Email + password signup. Creates a personal tenant + owner membership. |
| `POST` | `/api/v1/auth/login` | Email + password login. |
| `POST` | `/api/v1/auth/google` | Verify a Google ID token. Aud is checked in-process. |
| `GET` | `/api/v1/auth/me` | Current user + active tenant + all memberships. Requires bearer token. |

### Admin portal (Phases 2–4)

Every `/api/v1/admin/*` route requires an authenticated `owner` or `admin` role via `require_role("owner", "admin")`. Reads on `/admin/registry/*` are also open to `member`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/admin/connections` | List this tenant's connections |
| `POST` | `/api/v1/admin/connections` | Register a new connection (password encrypted before insert) |
| `GET` | `/api/v1/admin/connections/{id}` | |
| `PATCH` | `/api/v1/admin/connections/{id}` | |
| `DELETE` | `/api/v1/admin/connections/{id}` | |
| `POST` | `/api/v1/admin/connections/{id}/test` | Run the adapter-specific connectivity tester |
| `POST` | `/api/v1/admin/connections/{id}/scaffold` | Queue a schema inspection job (BackgroundTasks) |
| `GET` | `/api/v1/admin/scaffold-jobs` | List this tenant's jobs |
| `GET` | `/api/v1/admin/scaffold-jobs/{id}` | Poll status + progress + result summary |
| `GET` | `/api/v1/admin/registry/types` | Supported registry resource types |
| `GET` | `/api/v1/admin/registry/{type}` | List entries for the calling tenant |
| `GET` | `/api/v1/admin/registry/{type}/{name}` | Read YAML source + parsed JSON |
| `PUT` | `/api/v1/admin/registry/{type}/{name}` | Upsert — parses YAML, computes checksum, invalidates LRU |
| `DELETE` | `/api/v1/admin/registry/{type}/{name}` | Delete + invalidate LRU |

### Core (LLD 1–8)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/resolve` | Classify NL input + return QueryPlan |
| `GET` | `/api/v1/enums` | List all registered enums |
| `GET` | `/api/v1/enums/{name}` | Full enum definition |
| `GET` | `/api/v1/enums/{name}/options` | UI-ready dropdown options |
| `GET` | `/api/v1/enums/{name}/llm-context` | LLM-formatted plain text |
| `GET` | `/api/v1/enums/by-group/{group}` | Enums in a group |
| `POST` | `/api/v1/patterns/match` | Fuzzy match user input. Body: `{input, entity_hint?}` |
| `GET` | `/api/v1/patterns/{pattern_id}` | Full pattern definition |
| `GET` | `/api/v1/patterns/entity/{entity}` | All patterns for an entity |
| `GET` | `/api/v1/patterns/stats` | Hit rate, miss rate, top patterns |
| `GET` | `/api/v1/entities/{entity}` | Fetch entity list (sort/filter/page params) |
| `GET` | `/api/v1/entities/{entity}/{pk}` | Fetch single record |
| `GET` | `/api/v1/schema/{entity}/display` | DisplayConfig for entity |
| `GET` | `/api/v1/schema/{entity}/fields` | FieldMeta[] for entity |
| `GET` | `/api/v1/schema/{entity}/mutations` | MutationDef[] for entity |
| `GET` | `/api/v1/widgets` | All widgets + categories |
| `GET` | `/api/v1/widgets/{id}` | Single widget definition |
| `GET` | `/api/v1/widgets/dashboard` | Widgets grouped by category |
| `GET` | `/api/v1/widgets/entity/{entity}` | Widgets for an entity |
| `POST` | `/api/v1/widgets/{id}/execute` | Execute widget with params (0 LLM calls) |
| `POST` | `/api/v1/mutate/preview` | Generate diff preview (no DB write) |
| `POST` | `/api/v1/mutate/execute` | Execute confirmed mutation in transaction |

All endpoints: JWT-secured via API gateway. All endpoints: rate-limited.

---

## DynamoUI-Owned Database Tables

DynamoUI manages a small set of framework-owned tables in a separate `dynamoui` schema, managed by **Alembic migrations**. These are the only tables DynamoUI owns — business tables are never created or altered. Key table: `audit_log` — every mutation logged with user identity (from JWT), entity, operation, affected record PK.

---

## Architectural Rules

### Security — Non-Negotiable
- **Never store DB credentials in YAML.** For the bundled PG adapter use environment variables. For tenant-owned connections, the password MUST go through `backend.crypto.envelope.encrypt()` before hitting the database. The service layer (`ConnectionService`) enforces this — never call the DAO directly with a plaintext password.
- **Crypto single point of truth:** `cryptography.hazmat` is imported **only** in `backend/crypto/envelope.py`. Future adapters / subsystems reuse `encrypt()` / `decrypt()`; they don't re-import the library.
- **Never return plaintext credentials in a response DTO.** `ConnectionRead` exposes only `has_password: bool`. The decrypted dict produced by `ConnectionService.materialise(row)` is single-use — pass it straight to the adapter tester, never log it, never serialise it.
- `sensitive: true` fields: excluded from LLM context injection, masked as `***` in all logs. They ARE returned in query results to authorised users.
- `dynamoui_reader` for all queries. `dynamoui_writer` for mutations only. LLM never has access to either credential.
- Raw user input is **never logged** — log only SHA-256 hash for correlation.
- All queries built via SQLAlchemy Core's parameterised builders. **No string concatenation in query construction.**
- `ssl_mode='require'` in production.

### Multi-tenant invariants — Non-Negotiable
- **Tenant segregation at the DAO layer.** Every method on `AuthDAO`, `ConnectionDAO`, `ScaffoldJobDAO`, `RegistryDAO` takes `tenant_id` as an **explicit argument** and applies it as a WHERE clause. There is no ambient request context — the `dependencies` module is the ONLY place the JWT is decoded.
- **Cross-tenant access is a hard error.** Every new tenant-scoped DAO/service method MUST have a test that proves Tenant B can't read/update/delete Tenant A's rows. See `tests/test_tenant_connections.py`, `tests/test_tenant_scaffold.py`, `tests/test_tenant_registry_service.py` for the patterns to copy.
- **Token tenant is re-verified on every request.** `get_current_context` looks up the membership in `auth_tenant_users` after decoding the JWT. Do not cache this check — a revoked role must take effect immediately.
- **Tenant YAML registry mutations ALWAYS invalidate the LRU cache.** `RegistryService.upsert` / `delete` call `cache.invalidate(tenant_id)` before returning. Never skip this.
- **Bounded memory.** The registry cache is strictly LRU with `DYNAMO_TENANT_REGISTRY_CACHE_SIZE` as the hard cap. Do not introduce any other global dict keyed on `tenant_id` — use the same cache or extend it.
- **Roles live in `auth_tenant_users.role`.** Valid values: `owner`, `admin`, `member`, `viewer`. Do not invent a parallel authorisation scheme; use `require_role(...)` dependencies.

### Pattern Cache Rules
- `dynamoui compile-patterns` must run in CI on every skill file change.
- Pattern cache is **read-only** in Phase 1 — no write API exposed.
- `PatternPromoter.promote()` body is `pass` — Phase 2 stub. Do not implement.
- `DYNAMO_CACHE_AUTO_PROMOTE_ENABLED` must stay `false` in Phase 1.

### Intent Resolver
- Classifies NL input as: `READ`, `MUTATE`, `VISUALIZE`, `NAVIGATE`
- Rule engine runs first. LLM fallback (Claude Haiku via Anthropic API) only when rule engine confidence **< 0.8**.
- Input length capped at **500 characters**.
- Rate limit: **50 req/s per user**.

### Widget Execution (Zero LLM)
- Widget clicks bypass the Intelligence Layer entirely: Widget → PatternCache (direct in-process by `pattern_id`) → AdapterRegistry → PostgreSQLAdapter → UI Runtime.
- **Total LLM calls for any widget execution: 0.**
- Widgets respect entity `read_permissions` — hidden from dashboard if user lacks access (filtered at API level via JWT roles, not in frontend).
- Widget params sanitised before substitution into `query_template` placeholders.

### Mutations
- Mutations always require **explicit user confirmation with a diff preview**. Never skip this gate.
- `POST /api/v1/mutate/preview` does **not** write to DB — in-memory DiffBuilder only.
- All mutations execute within a database transaction with automatic rollback on failure.
- Notification channels: Email only in v1. Slack and Webhook feature-flagged **off**.

### Frontend — CSS Rules (ESLint enforced)
1. **No hardcoded colours.** All `bg-*`, `text-*`, `border-*` Tailwind classes must use `dui-` prefix.
2. **No inline `style={{ color: '...' }}`** for colours. Use Tailwind classes.
3. **No `var(--dui-*)` in JSX `style={}` props.** Exception: `BADGE_PALETTE` (programmatic colour).
4. **No Tailwind `dark:` prefix.** Dark mode handled entirely by theme CSS `@media (prefers-color-scheme: dark)` block.
5. Component-specific token first: `--dui-table-header-bg` before `--dui-surface-secondary`.

### Frontend — Accessibility (WCAG 2.1 AA)
- Inline editing **disabled on `sm` breakpoint** (< 640px) — edit via NL bar or DetailCard instead.
- `SensitiveCell`: renders as masked dots. Never in DOM as plaintext. Inline edit inputs for sensitive fields are disabled.
- Editable cell: `aria-label='{field}: double-click to edit'`. Escape cancels + returns focus. Enter commits.

### Adapter Extensibility
- New adapter: implement `DataAdapter` ABC + `SchemaInspector` + `SchemaValidator`, register in `adapters.registry.yaml`. **Zero framework changes.**
- Adapters that cannot support scaffold (e.g. future REST adapter) must raise `NotImplementedError` with a descriptive message.

---

## How Claude Code Should Minimise LLM Calls at Runtime

The architecture is specifically designed so the vast majority of user interactions never reach the LLM. Preserve and extend this:

1. **Write comprehensive NL triggers in every `*.patterns.yaml`.** More triggers per pattern = higher cache hit rate = fewer LLM calls. Write triggers without stopwords (`the`, `all`, `show`, etc.) since those are stripped before matching.

2. **Add widgets for all common queries.** Widget clicks are always 0 LLM calls. Any query that users run frequently should become a widget in `widgets.yaml`.

3. **Never lower the 0.90 fuzzy threshold** without an explicit design decision. Lowering it increases false-positive pattern matches; raising it pushes more traffic to the LLM fallback.

4. **Run `dynamoui compile-patterns` in CI** on every skill file change to keep hashes current. Stale hashes force patterns to be skipped, driving unnecessary LLM fallbacks.

5. **Use `format_for_llm()` plain text** (not JSON) when injecting enum context into LLM prompts. Lower token count = lower cost per LLM call that does occur.

6. **Scope fuzzy matching to entity hint.** When the Intent Resolver has identified the target entity, pass `entity_hint` to `PatternCache.lookup()`. This scopes the trigger index to that entity, improving match precision and avoiding cross-entity false positives.

---

## Testing

```bash
# Backend — all tests
pytest

# Backend — with coverage (target: 95% on models/ + loader/)
pytest --cov=skill_registry --cov=pattern_cache --cov=adapters --cov-report=term-missing

# Frontend
npm test

# Theme token validation (runs before Vite build in CI)
python scripts/validate_theme.py src/styles/theme.css

# Visual regression
npx chromatic
```

**Integration tests** require PostgreSQL:
```bash
docker run \
  -e POSTGRES_DB=dynamoui \
  -e POSTGRES_USER=dynamoui_reader \
  -e POSTGRES_PASSWORD=test \
  -p 5432:5432 postgres:16
```

**Performance gate:** Pattern cache lookup with 5,000 triggers must complete in **< 5ms** per lookup. Assert this in the performance test suite.

**Fixture reuse:** Always use `tests/fixtures/` YAML files. Do not create inline fixture data in individual test files — it diverges from the golden fixtures.

---

## What's Out of Scope (current release)

Do not implement or propose:

- MongoDB adapter
- REST/GraphQL adapters
- Slack/Webhook notification channels (feature-flagged off)
- Redis-backed pattern cache
- Hot-reload / inotify watch mode for skill files
- Per-user widget personalisation / drag-and-drop dashboard
- Visual diff preview modal upgrade (v1 uses plain-text table)
- Embedding-based semantic matching
- Full WYSIWYG layout editor
- Self-hosted LLM inference
- Virtual scrolling for large result sets
- Custom column renderers per entity
- Export functionality

### Intentionally deferred multi-tenant follow-ups

Documented in Phase 6 of [`docs/MULTI_TENANT_PLAN.md`](docs/MULTI_TENANT_PLAN.md):

- Real query / mutation execution for the cloud adapters (Phase 5 ships test + scaffold paths only; query stubs raise `NotImplementedError`).
- Monaco-backed YAML editor for the admin registry tab (currently a plain textarea to keep the admin bundle lean).
- Per-tenant key rotation CLI (`DYNAMO_CRYPTO_KEY_VERSION` is in place but the rewrap tool isn't).