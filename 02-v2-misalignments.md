# v2 Plan vs. Existing Architecture — Misalignments

Flagged during implementation. Each item explains what the plan assumes,
what the code actually is, and the judgment call I made. Please review and
tell me which ones to revisit.

## 1. Schema name: `dynamoui` vs. `dynamoui_internal`

- **Plan §1.1:** "A separate PostgreSQL schema `dynamoui` inside the *same*
  database… Configurable via `DYNAMO_OWNED_SCHEMA`."
- **Reality:** all existing internal tables (metering, auth, tenants,
  connections, scaffold, registry) already live in `dynamoui_internal`,
  configured via `DYNAMO_INTERNAL_DB_SCHEMA`.
- **Chosen path:** v2 tables (`dui_saved_view`, `dui_dashboard`,
  `dui_schedule`, `dui_alert`, `dui_delivery_run`, `dui_share_token`,
  `pattern_gap`) live in the **existing** `dynamoui_internal` schema,
  prefixed with `dui_` where the plan called for unprefixed names.
- **Why:** introducing a parallel schema would bifurcate migrations
  (two Alembic heads), pools, and schema-binding config. The prefix
  preserves the plan's intent (isolation from customer data) without
  the duplication tax.
- **Would flip if:** you want a literal separate schema. Set
  `DYNAMO_OWNED_SCHEMA` and have me add a second Alembic head.

## 2. Multi-tenancy: plan treats it as v3; code is already multi-tenant

- **Plan §8.3:** "Multi-tenancy… Out of scope for v2. All users share one
  tenant context."
- **Reality:** the existing `auth` subsystem has `auth_tenants`, `auth_users`,
  `auth_tenant_users` with roles, full JWT-verified `AuthContext` with
  tenant + role re-verified on every request, and per-tenant connection
  registries already in production.
- **Chosen path:** every v2 table carries `tenant_id`, every router uses
  `AuthContext` → `ctx.user.id` + `ctx.tenant.id`. Ownership is enforced
  in service queries.
- **Why:** writing single-tenant shim code now would mean rewriting every
  CRUD method when v3 lands in three months. The plan's promise that
  "multi-tenancy is an additive change" is already cheaper to honour than
  to defer.
- **Would flip if:** there's a product reason to keep saved views global
  rather than per-tenant.

## 3. Dedicated `dui_internal_engine` pool

- **Plan §1.3:** "A second dedicated pool `dui_internal_engine` for the
  `dynamoui` schema."
- **Reality:** the existing `auth_engine` (pool_size=2, max_overflow=3)
  already services metering, auth, connections, scaffold, registry — all
  in the same internal schema.
- **Chosen path:** v2 services share `auth_engine`.
- **Why:** opening another connection pool targeted at the same DB and
  schema doubles connections for no isolation benefit. The plan's concern
  ("a noisy customer query must never block a scheduled report") is
  already handled because customer queries go through the *adapter*
  engines (`reader_engine`/`writer_engine`), not `auth_engine`.
- **Would flip if:** you want scheduled writes isolated from auth/metering
  writes — I'd add a `dui_internal_engine` with its own pool for the
  scheduling/alert write path.

## 4. Intent classifier with five intents (READ | MUTATE | VISUALIZE | NAVIGATE | SCHEDULE)

- **Plan §5.7, §3.1:** assumes an existing Intent Resolver that the plan
  extends with a new `SCHEDULE` intent.
- **Reality:** the current `/resolve` endpoint does not classify intent —
  it pattern-cache-matches → LLM-synthesises and always produces READ-style
  results. No classifier exists.
- **Chosen path:**
  - The v2 resolve pipeline (`/api/v1/resolve/v2`) returns the
    discriminated-union `ResolutionResult` from the contract, but only
    emits the `executed` and `clarification_needed` kinds today. The
    `schedule_draft` / `alert_draft` / `mutation_preview` kinds are
    reachable only via the explicit slash commands (`/schedule`) or the
    existing `/mutate/preview` endpoint.
  - NL-to-schedule is wired under `/commands/dispatch` with
    `{command: "schedule", args: "..."}`.
- **Why:** introducing a full intent classifier is its own feature
  (≥ 1 week, the plan doesn't even describe it). Slash commands give a
  clean escape hatch without a half-built classifier in the main path.
- **Would flip if:** you want a real classifier. That's a new module I
  can scaffold — suggest putting it in `backend/query_engine/intent/`.

## 5. Legacy `/api/v1/resolve` kept alongside `/api/v1/resolve/v2`

- **Plan:** assumes the existing resolve endpoint evolves in-place with
  the new envelope.
- **Reality:** the existing `/resolve` is called by frontend code today
  and returns a different shape (`ResolveResponse`), metered via
  `operation_id`, etc. Rewriting it in place is a breaking client change.
- **Chosen path:** the v2 envelope + pipeline ships as `/api/v1/resolve/v2`;
  the legacy endpoint keeps working unchanged. Both return provenance-bearing
  payloads but only `/v2` returns the discriminated-union shape.
- **Why:** the interaction contract §10 explicitly allows both versions to
  coexist.
- **Would flip if:** you want to cut over hard — I can delete the old
  `/resolve` in one commit once the frontend is ready.

## 6. Verifier settings: defaults **disabled**

- **Plan §3.5:** "Defaults are safety-first: verify cache hits, skip
  self-verification of synthesised plans…" — i.e. `enabled=True` by default.
- **Reality / user ask:** "Ensure to make the new llm verification loop
  can be toggled through env variable." — combined with cost sensitivity,
  I set `DYNAMO_VERIFIER_ENABLED=false` as the default.
- **Chosen path:** the verifier is off until `DYNAMO_VERIFIER_ENABLED=true`
  is set. When off, the pipeline returns `verifierVerdict='skipped'` and
  `verifierVerified=false` in the provenance envelope — the frontend
  contract supports this explicitly.
- **Why:** the user's instruction is clear, and the v2 plan §3.9 itself
  lists "verifier cost exceeds savings" as a High-severity risk — starting
  off is the conservative default.
- **Would flip if:** you want default-on — change `enabled: bool = Field(False)`
  to `True` in `VerifierSettings`.

## 7. `dynamoui/migrations/` vs. existing `alembic/`

- **Plan §1.1:** "Alembic migrations live in `dynamoui/migrations/`."
- **Reality:** migrations are already in `alembic/versions/` with an
  existing chain (`001_metering` → … → `005_tenant_registry`).
- **Chosen path:** v2 migration is `20260417_006_create_v2_tables.py`
  chained onto `005_tenant_registry`. `alembic/env.py` updated to import
  the new metadata modules.

## 8. Pattern cache hot-reload on verifier gap resolution

- **Plan §3.6:** "operator clicks 'accept suggestion'… hot-reloading the
  pattern cache on success."
- **Reality:** the existing `PatternPromoter` has a hot-reload callback
  (`_on_pattern_promoted` in `main.py`). The gap recorder writes to a new
  `pattern_gap` table, but the operator-dashboard UI to act on those rows
  is **not** wired — the plan places it in Phase 4.
- **Chosen path:** gap recording writes (de-duped by `input_hash`) are
  implemented; the operator UI that turns them into patterns is explicitly
  out of scope here. When you're ready for that UI, the promoter+callback
  plumbing is already in place.

## 9. Parallel execution optimisation (§3.10)

- **Plan §3.10:** "For cache hits with very high confidence, start executing
  the candidate in parallel with verification."
- **Chosen path:** honoured via the `DYNAMO_VERIFIER_PARALLEL_EXECUTION`
  setting, but **not implemented** in this pass — the current pipeline is
  sequential (verifier → execute). The setting exists so wiring it later
  is additive.
- **Why:** correctness first. The cancellation-on-reject path requires
  SQLAlchemy-async query cancellation wiring in the adapter layer and is
  error-prone; I'd rather ship a correct sequential path and add parallel
  later behind the flag.

## 10. APScheduler worker

- **Plan §5.1/5.2:** APScheduler with `PostgreSQLJobStore` runs in-process.
- **Chosen path:** schedule + delivery-run tables and CRUD are in place,
  plus a `POST /api/v1/schedules/:id/test` that records a `delivery_run`
  row directly. **The actual cron tick loop / APScheduler is not wired**.
- **Why:** APScheduler is a runtime concern (needs the main event loop,
  careful shutdown, advisory locks) and its own well-defined work package.
  The data model and CRUD are all that's needed for the frontend to build
  the schedule-creation flow.
- **Would flip if:** you want the worker live — straightforward follow-up
  (~0.5 week) using the existing `auth_engine` + `APScheduler` against
  `dui_schedule.next_run_at`.

## 11. Layout validator: plan mentions `is_default` and total-area limit

- **Plan §2.4:** "widths in [1,12]… total area doesn't explode past 200
  cells."
- **Implementation:** overlap + column-overflow check is in; the 200-cell
  aggregate check is not enforced (requires tracking grid height).
- **Would flip if:** you want the aggregate check — one-liner in
  `dashboard_service._validate_layout`.

## 12. `delivery_run.schedule_id` / `alert_id` — plan suggests FKs

- **Plan §1.2:** `delivery_run (..., schedule_id, alert_id, ...)` with the
  comment "points at any first-class object".
- **Chosen path:** nullable columns, no FK. Either `schedule_id` or
  `alert_id` is set per row; enforcement is at the service layer.
- **Why:** exclusive-or FKs (`CHECK (schedule_id IS NULL <> alert_id IS NULL)`)
  are ugly and the existing schema avoids them. Flip if you'd rather have
  the check.

---

## Summary of env vars added

```
DYNAMO_VERIFIER_ENABLED=false              # master toggle — default OFF per your ask
DYNAMO_VERIFIER_VERIFY_CACHE_HITS=true
DYNAMO_VERIFIER_VERIFY_SYNTHESISED=false
DYNAMO_VERIFIER_VERIFY_TEMPLATES=true
DYNAMO_VERIFIER_VERIFY_SAVED_VIEWS=false
DYNAMO_VERIFIER_SKIP_ON_CONFIDENCE_ABOVE=0.98
DYNAMO_VERIFIER_SKIP_INTENTS=["NAVIGATE"]
DYNAMO_VERIFIER_LLM_TIMEOUT_MS=1500
DYNAMO_VERIFIER_ON_LLM_FAILURE=approve_candidate  # or reject_and_synth
DYNAMO_VERIFIER_VERIFIER_MODEL=                   # empty = share primary
DYNAMO_VERIFIER_VERDICT_CACHE_SIZE=2048
DYNAMO_VERIFIER_MONTHLY_BUDGET_USD=0              # 0 = unlimited
DYNAMO_VERIFIER_PARALLEL_EXECUTION=true           # config only; not wired

DYNAMO_FEATURE_PERSONALISATION=true
DYNAMO_FEATURE_PROVENANCE=true
DYNAMO_FEATURE_SCHEDULING=true
DYNAMO_FEATURE_ALERTS=true
DYNAMO_FEATURE_PALETTE=true
DYNAMO_FEATURE_SHARING=true
DYNAMO_FEATURE_EXPOSE_SQL=false                   # gate SQL in provenance
```

## What ships in this pass

| Milestone | Status |
|---|---|
| M1 Persistence foundation | Tables + migration ready; schema reuses `dynamoui_internal` |
| M2 Saved Views + Dashboards | CRUD + tile validation + `POST /views/:id/execute` |
| M3 LLM Verification Loop | **Full; toggled via `DYNAMO_VERIFIER_ENABLED`** |
| M4 Provenance API | Envelope + `/resolve/edit` + `/patterns/propose` |
| M5 Scheduled Delivery | CRUD + cron validator + NL-to-schedule; **cron worker not wired** |
| M6 Threshold Alerts | CRUD + condition evaluator pure-function |
| M7 Command Palette | `/commands/dispatch` + `/search` |
| M8 Shareable links | Token CRUD + `/shared/:token` + `/embed/:token` |

## What still needs you

1. Pick a side on each misalignment (§1–§12).
2. APScheduler worker if you want real delivery (M5 §10 above).
3. Operator dashboard for `pattern_gap` rows (plan §3.6 Phase 4 work).
