# claude-metering

Module: `backend/metering/`
Role: LLM cost tracking and operation auditing. Fire-and-forget — metering failures must never block request processing. Multi-tenant support is a Phase 2 stub.

## Key Classes

| Class | File | Purpose |
|---|---|---|
| `MeteringService` | `service.py` | High-level API: `start_operation()`, `complete_operation()`, `record_llm_interaction()`. All exceptions caught internally + logged as warnings. |
| `CostCalculator` | `cost.py` | `(input_tokens / 1000) × input_rate + (output_tokens / 1000) × output_rate`. Rates sourced from `cost_rates` table. |
| `MeteringLLMProvider` | `provider_decorator.py` | Decorator wrapping any `LLMProvider`. Records interaction + cost after each LLM call. Never raises — pure fire-and-forget. |
| `OperationDAO` | `dao/operation_dao.py` | CRUD for `metering_operations` table: `create()`, `update()`, `get_by_id()`. |
| `InteractionDAO` | `dao/interaction_dao.py` | CRUD for `metering_interactions` table: `create()`, `get_by_operation_id()`. |
| `CostRateDAO` | `dao/cost_rate_dao.py` | `get_rate(provider, model)` — returns current effective rate row. |

## Internal Schema Tables (`models/tables.py`)

All tables live in the `dynamoui_internal` schema (configurable via `DYNAMO_INTERNAL_SCHEMA`):

| Table | Key Columns |
|---|---|
| `metering_cost_rates` | provider, model, input_cost_per_1k, output_cost_per_1k, effective_from, effective_to |
| `metering_operations` | id, operation_type, tenant_id, session_id, user_id, entity, intent, pattern_id, cost_estimate, status, created_at |
| `metering_interactions` | id, operation_id (FK), provider, model, input_tokens, output_tokens, cost, created_at |

Tables defined with `sqlalchemy.MetaData` — Core only, no ORM.

## DTOs (`dto/`)

| DTO | File | Usage |
|---|---|---|
| `OperationCreateDTO` | `operation_dto.py` | Passed to `MeteringService.start_operation()` |
| `OperationUpdateDTO` | `operation_dto.py` | Passed to `MeteringService.complete_operation()` |
| `LLMInteractionCreateDTO` | `interaction_dto.py` | Passed to `MeteringService.record_llm_interaction()` |
| `CostRateDTO` | `cost_rate_dto.py` | Returned by `CostRateDAO.get_rate()` |

## Context Propagation

`metering/context.py` — async context var for threading `operation_id` through the call stack:
- `set_metering_context(operation_id)` — set at request start
- `get_metering_context()` — read inside `MeteringLLMProvider` to correlate interactions

## Operation Lifecycle

```
Request arrives
  → MeteringService.start_operation()     # creates operations row, returns operation_id
  → [business logic + LLM calls]
      → MeteringLLMProvider intercepts each LLM call
          → MeteringService.record_llm_interaction()   # creates interactions row
  → MeteringService.complete_operation()  # updates status + final cost_estimate
```

## API Endpoints (prefix: `/api/v1/metering`)

| Route | Notes |
|---|---|
| `GET /operations` | List operations (filterable by tenant, date range) |
| `GET /operations/{id}` | Single operation detail |
| `GET /interactions` | List LLM interactions (filterable by operation_id) |
| `GET /cost-rates` | Current provider/model cost rates |

## Schema Export

Run `python -m backend.metering.schema.export` to dump DDL for internal tables to stdout. Used when setting up a new environment.

## Migrations

Internal schema changes go through Alembic (`alembic/`). Target: `dynamoui_internal` schema only. Never use Alembic for business tables (DynamoUI doesn't own them).

## Critical Rules

- **Never block requests**: `MeteringService` swallows all exceptions. If metering DB is down, requests still succeed.
- **Fire-and-forget pattern**: all DAO calls are async but failures are caught at the service layer.
- **Multi-tenant stub**: `tenant_id` field exists on operations but enforcement is Phase 2. Don't add tenant isolation logic yet.
- **SQLAlchemy Core only**: same rule as adapters module — no ORM for internal tables.
- **Separate internal schema**: metering tables live in `dynamoui_internal`, never in the business schema.
