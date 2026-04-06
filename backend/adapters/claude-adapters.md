# claude-adapters

Module: `backend/adapters/`
Role: Abstract data query/mutation interface (`DataAdapter` ABC) + PostgreSQL implementation. DynamoUI reflects tables it does not own — SQLAlchemy Core only, never ORM.

## Key Classes

### Base (`adapters/base.py`)

| Class | Purpose |
|---|---|
| `QueryPlan` | Read operation descriptor: entity, filters, sort, pagination, joins, aggregations, select_fields, result_limit |
| `FilterClause` | `field`, `op` (eq/ne/gt/gte/lt/lte/in/like/is_null), `value` |
| `SortClause` | `field`, `dir` (asc/desc) |
| `JoinClause` | FK join: source field, target entity/field, join type (inner/left) |
| `AggregationClause` | count/sum/avg/min/max on field + alias |
| `QueryResult` | `rows` (list[dict]), `total_count`, `page`, `page_size` |
| `MutationPlan` | Write descriptor: entity, operation (create/update/delete), record (dict), affected_pk |
| `MutationResult` | `success`, `affected_count`, `message` |
| `DiffPreview` | Before/after diff for confirmation UI: `operation`, `before`, `after`, `warnings` |
| `DataAdapter` (ABC) | Interface: `execute_query()`, `fetch_single_record()`, `preview_mutation()`, `execute_mutation()`, `validate_schema()` |

### Registry (`adapters/registry.py`)

- `register_adapter(key, adapter)` — module-level dict singleton
- `get_adapter(key)` → `DataAdapter`
- `initialise_adapters()` — reads `adapters.registry.yaml`, creates and pools all adapters at startup

### PostgreSQL (`adapters/postgresql/`)

| Class | File | Purpose |
|---|---|---|
| `PostgreSQLAdapter` | `adapter.py` | Full `DataAdapter` impl. Composes all sub-components below. |
| `PostgreSQLEngine` | `engine.py` | Async connection pool. Separate read engine (`dynamoui_reader`) and write engine (`dynamoui_writer`). |
| `TableBuilder` | `table_builder.py` | Skill YAML → `sqlalchemy.Table`. Maps skill field types to SA column types. FKs resolved at query time via FK graph — **no `sa.ForeignKey` constraints** (DynamoUI doesn't own tables). |
| `QueryTranslator` | `query_translator.py` | `QueryPlan` → `sqlalchemy.select()`. Uses `FILTER_OPS` dict for all operators. No string concat — always parameterized. |
| `MutationExecutor` | `mutation_executor.py` | `MutationPlan` → `insert/update/delete` in a transaction. Rolls back automatically on failure. |
| `DiffBuilder` | `diff_builder.py` | In-memory diff — no DB write. Produces `DiffPreview` for the confirmation UI. |
| `SchemaValidator` | `schema_validator.py` | Validates skill YAML against live DB (column existence, types, nullability). Called by `dynamoui validate --check-connectivity`. |
| `SchemaInspector` | `schema_inspector.py` | Live DB table → skill YAML stub. Used by `dynamoui scaffold`. |
| `type_map.py` | — | Skill field types ↔ SA column types: string→Text, integer→BigInteger, float→Numeric, boolean→Boolean, date→DateTime, uuid→UUID, enum→String, json→JSON |

## Adapter Configuration

`adapters.registry.yaml` in repo root:
```yaml
adapters:
  - key: primary
    type: postgresql
    host: ...
    port: 5432
    database: ...
```

## Read/Write Separation

- **Read engine**: `dynamoui_reader` PostgreSQL user — `execute_query()`, `fetch_single_record()`
- **Write engine**: `dynamoui_writer` PostgreSQL user — `execute_mutation()` only
- Both engines are async pools (asyncpg driver via SQLAlchemy 2.0)

## Mutation Gate (Phase 1 Invariant)

Every mutation goes through two steps — this must never be collapsed into one:
1. `preview_mutation()` → `DiffPreview` shown to user
2. `execute_mutation()` → only runs after user confirms the diff

Never call `execute_mutation()` without a prior `preview_mutation()` confirmation.

## Critical Rules

- **SQLAlchemy Core, NOT ORM**: use `sa.Table`, `sa.select()`, `sa.insert()` etc. — no `declarative_base()`, no `Session`, no `relationship()`.
- **No SQL string concatenation**: all filters use the `FILTER_OPS` dict with parameterized clauses.
- **FK joins via FK graph, not SA constraints**: `JoinClause` is resolved at query time through the in-memory FK graph from `SkillRegistry`.
- **Separate credentials for read/write**: never use the write engine for queries.
- **Secrets via SecretStr**: connection credentials come from `pg_settings` — never hardcode.
