# DynamoUI v2 — Backend Implementation Plan

*Implementation plan for personal workspace, scheduled delivery, action rail, provenance, LLM-verify loop · Scoped to single Postgres adapter*

## 0. Scope and sequencing

This plan adds seven capability clusters to the existing FastAPI monolith. It **assumes Phase 1 is shipped and Phase 2 (LLM query synthesis, pattern promotion, mutation orchestrator) is underway**. The work below slots between Phase 2 and Phase 4, with one cross-cutting change — the **LLM verification loop** — that modifies the existing Query Engine pipeline described in LLD 5.

Milestones, each independently shippable:

| M | Cluster | Depends on | Weeks |
|---|---------|-----------|-------|
| M1 | Persistence foundation (users, dynamoui-owned schema, Alembic) | Phase 1 | 1 |
| M2 | Saved Views + Personal Dashboards | M1 | 2 |
| M3 | LLM Verification Loop (Query Engine change) | Phase 2 query synth | 2 |
| M4 | Provenance Drawer API + query plan echo | M3 | 1 |
| M5 | Scheduled Delivery (cron, worker, channels) | M2 | 2 |
| M6 | Threshold Alerts + Change Subscriptions | M5 | 1.5 |
| M7 | Command Palette / Slash Command API | M2, M3 | 1 |
| M8 | Shareable links + embedding tokens | M2 | 0.5 |

Total: ~11 weeks of backend work, two engineers in parallel. M3 is the highest-risk item and should start in parallel with M1 rather than waiting.

---

## 1. Persistence foundation (M1)

Until now, DynamoUI does not own any tables — it reads the customer's database through the adapter. The new features **require DynamoUI-owned state** (users, saved views, dashboards, schedules, alerts, job runs). This is the biggest architectural shift and must be done cleanly or it will leak everywhere.

### 1.1 Dedicated DynamoUI-owned schema

- A separate PostgreSQL schema `dynamoui` inside the *same* database the customer points at — not a second database. Keeps deployment simple.
- Configurable via `DYNAMO_OWNED_SCHEMA` env var; defaults to `dynamoui`. If the customer objects to schema pollution, they can point us at a separate DB via a second connection string.
- Alembic migrations live in `dynamoui/migrations/`. Run at startup if `DYNAMO_AUTO_MIGRATE=true`, otherwise fail loud and require the operator to run `dynamoui migrate` manually.
- Schema inspection in LLD 3's `SchemaInspector` must filter out the `dynamoui` schema when scaffolding — it's our plumbing, not customer data.

### 1.2 Core tables (owned by DynamoUI)

```
dynamoui.user              (id, email, display_name, sso_subject, role, created_at)
dynamoui.saved_view        (id, owner_user_id, name, nl_input, query_plan_json,
                            entity, result_shape, created_at, updated_at, is_shared,
                            pattern_id_hint)
dynamoui.dashboard         (id, owner_user_id, name, description, layout_json,
                            is_default, created_at, updated_at)
dynamoui.dashboard_tile    (id, dashboard_id, source_type, source_id, position_x,
                            position_y, width, height, overrides_json)
dynamoui.pin               (id, user_id, source_type, source_id, position, created_at)
dynamoui.schedule          (id, owner_user_id, source_type, source_id, cron_expr,
                            timezone, channel, channel_config_json, format, enabled,
                            last_run_at, next_run_at, failure_count)
dynamoui.alert             (id, owner_user_id, saved_view_id, condition_json,
                            check_cron, channel, channel_config_json, enabled,
                            last_triggered_at, last_check_at)
dynamoui.delivery_run      (id, schedule_id, alert_id, started_at, finished_at,
                            status, rows_delivered, error_text, latency_ms)
dynamoui.share_token       (id, source_type, source_id, token_hash, expires_at,
                            created_by_user_id, access_count)
```

`source_type` is always one of `saved_view | widget | dashboard | pattern_result`. `source_id` refers to the respective table. This lets pins, schedules, and dashboards point at any first-class object without separate tables.

### 1.3 Connection pooling

- A second dedicated pool `dui_internal_engine` for the `dynamoui` schema. The existing `reader_engine` and `writer_engine` pools continue to serve customer data queries.
- This separation matters because **a noisy customer query must never block a scheduled report write**.

### 1.4 Auth

- Pluggable auth module `dynamoui/auth/`. Default: a dev-only `X-DynamoUI-User` header for local development. Production: OIDC/Google SSO (Phase 4 in the existing roadmap, but we need *something* now).
- FastAPI dependency `get_current_user()` returns a `User` object from the `user` table, provisioning on first sight for SSO flows.
- All new endpoints require authentication. Existing `/entities/*` endpoints gain an optional `user_id` context that gets written into audit logs.

---

## 2. Saved Views + Personal Dashboards (M2)

### 2.1 Domain model

A **SavedView** is a persisted `(nl_input, resolved_query_plan, entity, result_shape)` tuple, owned by a user. The `nl_input` is kept verbatim so we can re-resolve on schema change; the `query_plan_json` is the cached execution plan to skip resolution when the schema hash hasn't changed.

A **Dashboard** is an ordered collection of tiles. Each tile points to a `SavedView`, a `Widget` (from the existing widget registry), or a raw `pattern_id`. Tiles carry layout (grid coords, w/h) and optional overrides (custom title, refresh interval).

A **Pin** is a lightweight "show on my home" marker, used for the default home dashboard without requiring the user to explicitly create one.

### 2.2 Service layer

New module `dynamoui/personalisation/`:

```
personalisation/
├── services/
│   ├── saved_view_service.py     # CRUD + resolve-and-execute
│   ├── dashboard_service.py      # CRUD + layout validation
│   ├── pin_service.py
│   └── home_composer.py          # Assembles "my home" response
├── models/
│   └── schemas.py                # Pydantic request/response models
└── api/
    └── rest_router.py
```

**Critical design point — saved view execution flow:**

When a saved view is executed (via `POST /api/v1/views/{id}/execute` or inside a dashboard tile load), the service:

1. Compares the stored `skill_hash` against the current skill YAML hashes for the entities referenced in `query_plan_json`.
2. If **unchanged**: execute the stored plan directly through the adapter. **No LLM call, no pattern lookup.** This is the fast path and must be the default.
3. If **changed**: re-run the full resolve pipeline against `nl_input`, producing a new plan. If the new plan matches the stored one (field-wise), silently update the stored hash. If it differs, mark the saved view `stale: true` and surface a "Schema changed, re-confirm this view" affordance on the frontend — do not auto-execute.

This keeps personalisation resilient to schema drift without silently changing what the user thinks they saved.

### 2.3 REST endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/views` | List current user's saved views. Supports `?entity=` filter, `?shared=true` to see shared. |
| POST | `/api/v1/views` | Create. Body: `{name, nl_input, query_plan, entity, result_shape}`. Server stores `skill_hash` and pattern-ID hint if known. |
| GET | `/api/v1/views/{id}` | Get one. |
| PATCH | `/api/v1/views/{id}` | Rename, toggle share. |
| DELETE | `/api/v1/views/{id}` | Hard delete, cascades schedules and alerts. |
| POST | `/api/v1/views/{id}/execute` | Execute the view, returns `QueryResult` + `ProvenanceMeta` (see M4). |
| GET | `/api/v1/dashboards` | List current user's dashboards. |
| POST | `/api/v1/dashboards` | Create. |
| GET | `/api/v1/dashboards/{id}` | Full tree: dashboard + tiles + resolved display configs. |
| PATCH | `/api/v1/dashboards/{id}` | Rename, reorder tiles, update layout. Atomic. |
| POST | `/api/v1/dashboards/{id}/tiles` | Append a tile. |
| DELETE | `/api/v1/dashboards/{id}/tiles/{tile_id}` | Remove a tile. |
| GET | `/api/v1/home` | The composed personal home — system widgets + pinned items + default dashboard summary. |
| POST | `/api/v1/pins` | Pin any `{source_type, source_id}`. |
| DELETE | `/api/v1/pins/{id}` | Unpin. |

### 2.4 Dashboard layout validation

Layouts are stored as `layout_json` containing `{grid: "12col", tiles: [{tile_id, x, y, w, h}]}`. Validation at `PATCH` time: no overlaps, all tiles within bounds, widths in `[1, 12]`, total area doesn't explode past a sanity limit (200 cells). Reject with 422 on violation rather than silently fixing — the frontend is the source of layout truth.

### 2.5 Sharing model

`SavedView.is_shared=true` makes the view readable by any authenticated user but only writable by the owner. Shared dashboards are v3; for v2 the share path is via **share tokens** (M8).

---

## 3. LLM Verification Loop (M3) — the cross-cutting change

This is the feature you specifically asked for, and it changes the contract of the existing Query Engine. Done right, it becomes DynamoUI's defining technical claim: **"Every answer is LLM-verified, even when the LLM didn't write the query."**

### 3.1 The new pipeline

The current LLD 5 pipeline is:

```
input → normalise → classify → pattern_match → (cache hit: execute) OR (miss: LLM synth → execute)
```

The new pipeline is:

```
input → normalise → classify → candidate_resolution → LLM_VERIFIER → execute
                                        ↓
                                   returns one of:
                                   (a) pattern-cache hit    (candidate_source=cache)
                                   (b) widget / viz template (candidate_source=template)
                                   (c) LLM-synthesised plan  (candidate_source=synthesised)
                                   (d) saved-view match      (candidate_source=saved_view)
```

The `LLM_VERIFIER` is a new stage between candidate resolution and execution. It receives:

- The raw user input
- The candidate `QueryPlan` or action
- A summary of what the candidate will do, in structured form (entity, fields, filters, joins, aggregations, limit)
- Relevant skill YAML excerpts for involved entities

It returns one of three verdicts:

```python
class VerifierVerdict(Enum):
    APPROVE = "approve"             # candidate is correct, execute
    REJECT_PREFER_LLM = "reject"    # candidate is wrong, prefer LLM's own plan
    APPROVE_WITH_NOTE = "approve_with_note"  # correct but improvement suggested
```

On `REJECT`, the LLM also produces:
- Its own `QueryPlan` (which is now the plan that gets executed)
- A `pattern_gap_suggestion` — structured hint for how to close the gap (a new `nl_trigger` to add to an existing pattern, or a new pattern to seed)

### 3.2 Implementation location

New module `dynamoui/query_engine/verifier/`:

```
verifier/
├── llm_verifier.py         # Core verifier class
├── prompts.py              # Versioned prompt templates
├── verdict.py              # Verdict dataclass + enum
├── gap_recorder.py         # Writes to pattern_gap_queue
└── config.py               # Thresholds, toggles, caching
```

### 3.3 The verifier, in detail

```python
class LLMVerifier:
    def __init__(
        self,
        llm_client: LLMProvider,
        skill_registry: SkillRegistry,
        gap_recorder: PatternGapRecorder,
        verdict_cache: VerdictCache,
        settings: VerifierSettings,
    ):
        ...

    async def verify(
        self,
        user_input: str,
        candidate: CandidateResolution,
        context: ResolutionContext,
    ) -> VerifiedResolution:
        # 1. Fast exit: if verifier disabled for this intent/entity, approve.
        if not self._should_verify(candidate, context):
            return VerifiedResolution.approved(candidate, verified=False)

        # 2. Verdict cache lookup — input-hash + plan-hash + skill-hash keyed.
        cached = self._verdict_cache.get(user_input, candidate, context)
        if cached is not None:
            return cached

        # 3. Build minimal prompt — only fields + enums referenced in the plan.
        prompt = self._build_prompt(user_input, candidate, context)

        # 4. Call LLM with structured output mode.
        raw = await self._llm_client.verify(prompt)
        verdict = self._parse_verdict(raw)

        # 5. On REJECT, record a gap entry and return the LLM's own plan.
        if verdict.verdict == Verdict.REJECT_PREFER_LLM:
            await self._gap_recorder.record(
                user_input=user_input,
                rejected_candidate=candidate,
                llm_plan=verdict.llm_plan,
                gap_suggestion=verdict.pattern_gap_suggestion,
                context=context,
            )
            result = VerifiedResolution.from_llm_override(verdict)
        else:
            result = VerifiedResolution.approved(candidate, verified=True, note=verdict.note)

        self._verdict_cache.put(user_input, candidate, context, result)
        return result
```

### 3.4 Verdict caching — the performance-critical piece

A naive implementation calls the LLM on every query, doubling cost and latency. The cache is what makes this practical.

- **Key**: `sha256(normalised_input + plan_hash + skill_registry_hash)`
- **Value**: the full `VerifiedResolution` object.
- **Store**: in-memory LRU for Phase 2, Redis in Phase 4 (shared across replicas).
- **TTL**: None — invalidated only when `skill_registry_hash` changes (which changes the key automatically).
- **Warm-start**: on boot, the top N highest-frequency `(input, plan)` pairs from the last 7 days of query logs are pre-fetched into cache via a batch verifier run.

Result: the same cached query path runs through the verifier exactly once per skill revision. Second time, it's an in-memory hash lookup.

### 3.5 When the verifier runs (and when it doesn't)

Verifier behaviour is controlled by `VerifierSettings`:

```python
class VerifierSettings:
    enabled: bool = True
    verify_cache_hits: bool = True           # verify pattern matches
    verify_synthesised: bool = False         # LLM wrote it, don't re-verify its own work
    verify_templates: bool = True            # widgets, viz templates — yes, same class of risk
    verify_saved_views: bool = False         # user already saved this, don't re-question
    skip_on_confidence_above: float = 0.98   # very high-confidence pattern matches skip verification
    skip_for_intents: list[Intent] = []      # allow ops to exclude e.g. NAVIGATE
    llm_timeout_ms: int = 1500               # tight budget
    on_llm_failure: str = "approve_candidate" # or "reject_and_synth" — graceful degradation
```

Defaults are safety-first: verify cache hits, skip self-verification of synthesised plans (the LLM literally just wrote them), skip saved views (the user curated them), skip high-confidence pattern hits (>0.98 — these are near-exact matches).

### 3.6 Gap recorder and operator queue

When the verifier rejects a candidate, the rejection is recorded in a new table:

```
dynamoui.pattern_gap
  (id, user_input, rejected_candidate_json, llm_plan_json, gap_suggestion_json,
   entity, user_id, resolved, resolution_type, reviewed_by_user_id, reviewed_at,
   created_at, occurrence_count)
```

`occurrence_count` is incremented on duplicate gaps (same `(input_hash, entity)`) so the operator queue is ranked by real impact, not novelty.

The operator dashboard (Phase 4 in the existing roadmap) gets a new tab "Pattern Gaps" where operators:

1. See the user input, what the pattern matched, what the LLM preferred, and what the LLM suggests.
2. Can one-click "accept suggestion" — which appends an NL trigger to an existing pattern file, or creates a new pattern via the `PatternPromoter` flow, hot-reloading the pattern cache on success.
3. Can dismiss the gap (marks `resolved=true, resolution_type='dismissed'`).

This is the missing feedback loop that turns the verifier from a cost centre into a learning system. Every rejection makes the next query cheaper.

### 3.7 Prompt design

The verifier prompt is short and structured. Template (versioned via `prompts.py`):

```
System:
You are a query correctness verifier for DynamoUI. Your job is to decide
whether a proposed query plan correctly answers the user's natural-language
request against the given schema. Return JSON matching the schema provided.

User intent: {user_input}

Proposed plan:
- Entity: {entity}
- Fields returned: {fields}
- Filters: {filters}
- Joins: {joins}
- Aggregations: {aggregations}
- Limit: {limit}

Plan source: {candidate_source}  (cache | template | synthesised | saved_view)

Relevant schema:
{skill_yaml_excerpt}

Relevant enum values:
{enum_excerpt}

Decide: does this plan correctly answer the user's request?

Respond with JSON:
{
  "verdict": "approve" | "reject" | "approve_with_note",
  "reason": "<one sentence>",
  "llm_plan": <QueryPlan JSON, only if reject>,
  "pattern_gap_suggestion": {
    "suggestion_type": "add_trigger" | "new_pattern" | "refine_description",
    "target_pattern_id": "<existing id if add_trigger>",
    "proposed_nl_trigger": "<string>",
    "proposed_pattern_body": <pattern YAML dict, only if new_pattern>
  }
}
```

Model choice: **Claude Haiku** for verification (fast, cheap), **Claude Sonnet** for the primary synthesis path. The existing LLM provider abstraction (`DYNAMO_LLM_PROVIDER`) already supports this routing; we add a `DYNAMO_LLM_VERIFIER_MODEL` override.

### 3.8 Observability

Every verifier call emits a structured log:

```
{
  event: "verifier.verdict",
  user_input_hash, candidate_source, verdict, cache_hit, latency_ms,
  llm_cost_usd, entity, user_id
}
```

The operator cost dashboard (Phase 4) adds a "Verifier" widget: approval rate, rejection rate by candidate source, cache hit rate, cost per day. **Key metric**: rejection rate on cache hits — if this climbs, the pattern cache has drifted from user intent and needs review.

### 3.9 Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Verifier doubles query latency | High | Aggressive verdict caching + `skip_on_confidence_above` threshold + parallel execution (see §3.10) |
| Verifier cost exceeds savings | High | Haiku-class model only; per-tenant monthly budget with circuit breaker |
| Verifier rejects valid plans (over-rejection) | Medium | Operator dashboard shows rejection rate; ops can tune `verify_cache_hits=false` if noisy |
| LLM hallucinates a worse plan on reject | Medium | `llm_plan` goes through the same `QueryPlan` validator as any synthesised plan before execution |
| Verifier disagreement creates user confusion | Medium | Provenance drawer (M4) shows the verdict transparently; users understand "the system double-checked itself" |

### 3.10 Parallel execution — a latency optimisation

For cache hits with very high confidence (but below the skip threshold), **start executing the candidate in parallel with verification**. If the verifier approves, return the already-running query result. If it rejects, cancel the adapter query (SQLAlchemy async supports this), execute the LLM's plan instead, and return that.

This keeps p95 latency for the common case (cache hit + approve) roughly the same as today.

---

## 4. Provenance API (M4)

Every query execution already produces metadata internally. We need to expose it.

### 4.1 Response envelope change

All query execution responses (NL resolve, view execute, widget execute, dashboard tile execute) gain a `provenance` field:

```json
{
  "result": { "entity": "...", "rows": [...], "total_count": ..., ... },
  "provenance": {
    "candidate_source": "cache",
    "pattern_id": "Employee.senior_hires",
    "pattern_match_confidence": 0.96,
    "verifier_verdict": "approve",
    "verifier_verified": true,
    "verifier_latency_ms": 42,
    "verifier_cache_hit": true,
    "synthesised": false,
    "synthesis_confidence": null,
    "query_plan": { ... full QueryPlan JSON ... },
    "generated_sql": "SELECT ... FROM employee WHERE ...",
    "execution_latency_ms": 14,
    "adapter": "postgresql",
    "skill_hash": "a3f9...",
    "llm_cost_usd": 0.0003,
    "timestamp": "2026-04-17T10:15:00Z"
  }
}
```

`generated_sql` is gated by a feature flag `expose_sql` because some customers won't want SQL exposed to end users. Default: on in dev, off in prod.

### 4.2 "Edit as NL" endpoint

`POST /api/v1/resolve/edit` — takes a `query_plan` and returns the same plan with an editable NL form the frontend can drop into the input bar. For cache hits and saved views, this is the stored `nl_input`. For synthesised plans, the LLM reverse-translates the plan to NL for editing. This is a small but magical feature — it lets users drill into any result and tweak it.

### 4.3 "Propose as pattern" endpoint

`POST /api/v1/patterns/propose` — end users can request that a successful novel query be promoted. Creates a pattern proposal in the existing `pattern_review_queue` (from Phase 2 `PatternPromoter`) with `source='user_proposal'`. No immediate effect — operator reviews before commit.

---

## 5. Scheduled Delivery (M5)

### 5.1 Architecture

Schedules are cron expressions pointing at `SavedView` or `Dashboard` objects. A background worker executes them and ships results through the Notification Bus.

```
dynamoui/scheduling/
├── models/
│   └── schedule.py             # Schedule, DeliveryRun ORM models
├── cron/
│   ├── scheduler.py            # APScheduler wrapper, persisted jobs
│   └── cron_parser.py          # Validates cron expressions
├── workers/
│   ├── delivery_worker.py      # Executes a schedule, emits through Notification Bus
│   └── formatters/             # CSV, XLSX, HTML, PDF snapshot
│       ├── csv_formatter.py
│       ├── xlsx_formatter.py
│       └── html_formatter.py   # Embeds rendered table/chart as inline HTML for email
├── services/
│   └── schedule_service.py     # CRUD
└── api/
    └── rest_router.py
```

### 5.2 Scheduler choice

**APScheduler** with a **PostgreSQLJobStore** pointing at `dynamoui.schedule`. Rejected alternatives:

- **Celery Beat**: adds Redis/RabbitMQ infra. Our architecture principle is single-deployable monolith; adding a broker breaks it.
- **Cron + webhook**: requires external ops. We're shipping a product, not a config guide.
- **Rolling own scheduler with a DB poll**: feasible but reinvents the wheel; APScheduler has 10+ years of edge cases baked in.

APScheduler runs in the same FastAPI process but in a separate thread pool. At scale we add an optional deployment mode where `dynamoui-scheduler` runs as a dedicated process pointing at the same DB — same codebase, different entry point.

### 5.3 Cron validation

Accept 5-field cron (`minute hour dom month dow`) plus presets (`@hourly`, `@daily`, `@weekly`, `@monthly`). Reject sub-minute granularity (`* * * * *` every minute) outright — this is analytics delivery, not a real-time feed, and per-minute runs will melt the LLM verifier. Minimum interval: 15 minutes, configurable.

Timezones stored per schedule. UI shows preview of next 5 fire times in user's TZ on create/edit.

### 5.4 Delivery execution flow

```
Worker tick
  → fetch due schedules
  → for each schedule:
      → acquire advisory lock on schedule_id (prevents dup runs in HA)
      → create delivery_run row (status=running)
      → resolve source (SavedView or Dashboard)
      → execute (uses SavedViewService.execute — same path as user-triggered)
      → [VERIFIER NOTE: scheduled runs set verify_saved_views=false explicitly;
         the user curated this already and we don't want a 3am verifier flap
         rejecting their schedule]
      → format output (CSV / XLSX / HTML snapshot)
      → dispatch via Notification Bus (email in v1, Slack/webhook flagged)
      → update delivery_run (status=success, rows_delivered, latency_ms)
  → on failure: increment schedule.failure_count
  → if failure_count > threshold (default 5): disable schedule, send owner a notification
```

### 5.5 Channels

The Notification Bus from the existing PRD handles email only in v1. For scheduled delivery we extend it with:

- `ChannelAdapter` interface: `dispatch(recipient, subject, body, attachments) -> DispatchResult`
- `EmailAdapter` (SMTP) — default, v1.
- `SlackAdapter` and `WebhookAdapter` — remain feature-flagged per the existing roadmap, but the hooks are wired now so enabling is a flag flip.

### 5.6 REST endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/schedules` | List current user's schedules. |
| POST | `/api/v1/schedules` | Create. Body: `{source_type, source_id, cron_expr, timezone, channel, channel_config, format}`. |
| GET | `/api/v1/schedules/{id}` | Details including `next_run_preview`. |
| PATCH | `/api/v1/schedules/{id}` | Update cron, channel, enable/disable. |
| DELETE | `/api/v1/schedules/{id}` | Hard delete. |
| POST | `/api/v1/schedules/{id}/test` | Fire now, one-shot; bypasses cron, writes a `delivery_run`, but delivers via the chosen channel. |
| GET | `/api/v1/schedules/{id}/runs` | Recent delivery runs with pagination. |

### 5.7 NL-to-schedule

The killer feature for this cluster. Intent Resolver gains a fifth intent: `SCHEDULE`. When a user types "email me a chart of department headcount weekly":

1. Rule engine classifies as `SCHEDULE` (new keywords: `every`, `weekly`, `daily`, `email me`, `send me`, `schedule`).
2. The classifier extracts the sub-intent (here: `VISUALIZE` — a chart).
3. Sub-intent is resolved normally, producing a `QueryPlan` + chart config.
4. A scheduling parser extracts the cadence and channel: `"weekly"` → `"0 9 * * MON"`, `"email me"` → current user's email.
5. The full resolution returns a **draft schedule** — not saved, just returned to the frontend as a confirmation preview.
6. Frontend shows a modal: "I'll send you a chart of department headcount every Monday at 9am, to alice@corp.com. Confirm?" User clicks yes, frontend hits `POST /api/v1/schedules`, done.

No competitor in camps A/B/C does this in one sentence. This is the demo that sells the product.

Implementation: new module `dynamoui/scheduling/nl_parser.py` with explicit cron phrase library plus an LLM fallback for novel phrasings. LLM verifier approves/rejects the parsed cron+channel combination against the original user input — same pipeline as queries.

---

## 6. Threshold Alerts + Change Subscriptions (M6)

### 6.1 Alerts

An **Alert** is a saved view + a condition + a check cadence. Conditions are structured:

```json
{
  "type": "row_count",
  "operator": "gt",
  "value": 0
}
```
or
```json
{
  "type": "any_row_field",
  "field": "salary",
  "operator": "gt",
  "value": 300000
}
```
or
```json
{
  "type": "aggregate",
  "aggregate": "sum",
  "field": "amount",
  "operator": "gt",
  "value": 1000000
}
```

Alert evaluation runs on the same scheduler infrastructure as scheduled delivery, but the worker checks the condition before dispatching and only fires if it's true AND differs from the last evaluation (debouncing).

### 6.2 Change subscriptions

"Notify me when someone joins Platform Engineering" is a change subscription. Implementation for v2 is **polling-based**:

- The worker stores the last-seen PK set for the saved view.
- On each run, computes the diff (new PKs, removed PKs, changed PKs — the last requires a content hash, scoped to fields the user declared interest in).
- Fires a notification describing the diff.

CDC via logical replication is a v3 item — polling is good enough for "new employees this week" style use cases at the cadences people actually want (hourly to daily).

### 6.3 Alert condition NL parsing

Similar to schedule NL parsing: "alert me when any salary exceeds $300k" → parses to an alert spec. Verifier checks that the parsed condition matches the user's intent before save.

---

## 7. Command Palette / Slash Command API (M7)

Most of this is frontend work. Backend contributes:

### 7.1 Universal search

`GET /api/v1/search?q=<query>&types=entity,view,dashboard,widget,pattern&limit=20`

Returns a unified list of matching resources. Entity matching reuses the existing entity alias index from Intent Resolver. Views/dashboards/widgets matched by name with RapidFuzz.

Response shape:

```json
{
  "results": [
    {"type": "saved_view", "id": "...", "name": "...", "score": 0.95, "owner": "..."},
    {"type": "entity", "id": "Employee", "name": "Employee", "score": 0.90},
    ...
  ]
}
```

### 7.2 Slash command dispatcher

`POST /api/v1/commands/dispatch` — body: `{command: "chart", args: "headcount by dept"}`. Maps:

| Command | Behaviour |
|---|---|
| `/chart <nl>` | Forces VISUALIZE intent regardless of classification. |
| `/table <nl>` | Forces READ intent, tabular display. |
| `/schedule <nl>` | Forces SCHEDULE intent. |
| `/save <name>` | Saves the last query as a view. |
| `/pin` | Pins the last result to home. |
| `/export csv\|xlsx` | Exports the last query's result. |

"Last query" requires a per-user session context — a short-lived record of the user's recent query. New table `dynamoui.user_session_context` (in-memory Redis in Phase 4, but Phase 2/3 a simple TTL'd Postgres row is fine).

---

## 8. Shareable links + embedding (M8)

### 8.1 Share tokens

`POST /api/v1/share-tokens` — body: `{source_type, source_id, expires_in_seconds, max_access_count}`. Returns a URL-safe opaque token.

`GET /api/v1/shared/{token}` — no auth; renders the view/dashboard if token valid. Records an access in `share_token.access_count`.

The token is a random 32-byte value, hashed with bcrypt in the DB. Per-token rate limits and expiration enforce safety.

### 8.2 Embed endpoint

`GET /embed/{token}` — serves a minimal HTML page rendering the view/dashboard with no nav chrome, suitable for iframe embedding in Notion, Confluence, email. Uses the same apiClient flow as the full app but with a restricted context.

---

## 9. Cross-cutting: observability

Every new endpoint and worker emits structured logs with at minimum: `user_id`, `source_type/id`, `latency_ms`, `verifier_verdict`, `candidate_source`, `error`. The Phase 4 operator dashboard gets four new widgets: Saved Views (count, top entities), Schedules (running, failing), Alerts (triggered today), Verifier (approval/rejection, cost).

## 10. Cross-cutting: rate limiting

- Verifier: per-tenant monthly budget with a circuit breaker. When exceeded, `on_budget_exceeded="approve_candidate"` — degrades to pre-verifier behaviour rather than failing queries.
- Schedules/alerts: hard cap of 100 active per user. Prevents a runaway user from DoSing the worker.
- Share tokens: configurable expiration and max-access cap enforced at the service layer.

## 11. Migrations and rollout

All new tables live in the `dynamoui` schema. Alembic migrations are additive and reversible. Feature flags gate each cluster:

```
DYNAMO_FEATURE_PERSONALISATION=true   # M2
DYNAMO_FEATURE_VERIFIER=true          # M3
DYNAMO_FEATURE_PROVENANCE=true        # M4
DYNAMO_FEATURE_SCHEDULING=true        # M5
DYNAMO_FEATURE_ALERTS=true            # M6
DYNAMO_FEATURE_PALETTE=true           # M7
DYNAMO_FEATURE_SHARING=true           # M8
```

Ship dark behind flags; canary one customer per cluster before fleet rollout. The verifier especially needs cost/latency observation in a real environment before default-on.

---

## 12. Implementation report (2026-04-17)

v2 landed in one pass. Twelve misalignments between this plan and the pre-existing
architecture were flagged during implementation — see
[02-v2-misalignments.md](./02-v2-misalignments.md) for the full writeup with
a pick-a-side recommendation for each.

### What shipped

| Milestone | Status | Notes |
|---|---|---|
| M1 Persistence foundation | ✅ tables + migration | Reused existing `dynamoui_internal` schema with `dui_` prefix instead of a new `dynamoui` schema |
| M2 Saved Views + Dashboards | ✅ CRUD, tiles, layout validator, `/views/:id/execute` | Stale-on-skill-hash-change detection implemented |
| M3 LLM Verification Loop | ✅ full module, env-toggled | **Default `DYNAMO_VERIFIER_ENABLED=false`** per ask; parallel-execution optimisation §3.10 deferred |
| M4 Provenance API | ✅ envelope, `/resolve/edit`, `/patterns/propose` | `generatedSql` gated by `DYNAMO_FEATURE_EXPOSE_SQL` |
| M5 Scheduled Delivery | ✅ CRUD + cron validator + NL-to-schedule | APScheduler worker tick loop deferred to a follow-up |
| M6 Threshold Alerts | ✅ CRUD + pure-function condition evaluator | Worker integration waits for M5 worker |
| M7 Command Palette | ✅ `/commands/dispatch`, `/search` | Intent classifier not built — slash commands instead |
| M8 Shareable Links | ✅ token CRUD + `/shared/:token` + `/embed/:token` | bcrypt-if-available, SHA-256 fallback |

### Top misalignments requiring your call

1. **Schema name** — `dynamoui` (plan) vs. `dynamoui_internal` (existing). Chose
   existing schema with `dui_` prefix.
2. **Multi-tenancy** — plan scopes it to v3, but `auth_tenants` + per-tenant
   context already exist. Wired v2 tables as multi-tenant from day one.
3. **Verifier default** — plan recommends on; your ask and §3.9 risk-register
   argue for off. Defaulted to off.
4. **Intent classifier** — plan assumes one exists; none does. `SCHEDULE`
   routed via `/commands/dispatch` instead of classification.
5. **Legacy `/resolve`** — kept unchanged; v2 envelope lives at `/resolve/v2`.
6. **APScheduler worker** — data model + CRUD shipped; tick loop is a clean
   follow-up (<1 week).

### New env vars

```
# Verifier — master toggle plus full policy surface
DYNAMO_VERIFIER_ENABLED=false
DYNAMO_VERIFIER_VERIFY_CACHE_HITS=true
DYNAMO_VERIFIER_VERIFY_SYNTHESISED=false
DYNAMO_VERIFIER_VERIFY_TEMPLATES=true
DYNAMO_VERIFIER_VERIFY_SAVED_VIEWS=false
DYNAMO_VERIFIER_SKIP_ON_CONFIDENCE_ABOVE=0.98
DYNAMO_VERIFIER_LLM_TIMEOUT_MS=1500
DYNAMO_VERIFIER_ON_LLM_FAILURE=approve_candidate
DYNAMO_VERIFIER_VERIFIER_MODEL=
DYNAMO_VERIFIER_VERDICT_CACHE_SIZE=2048
DYNAMO_VERIFIER_MONTHLY_BUDGET_USD=0
DYNAMO_VERIFIER_PARALLEL_EXECUTION=true

# Feature flags per cluster
DYNAMO_FEATURE_PERSONALISATION=true
DYNAMO_FEATURE_PROVENANCE=true
DYNAMO_FEATURE_SCHEDULING=true
DYNAMO_FEATURE_ALERTS=true
DYNAMO_FEATURE_PALETTE=true
DYNAMO_FEATURE_SHARING=true
DYNAMO_FEATURE_EXPOSE_SQL=false
```

### Still needs you

1. Decision on each of the twelve misalignments (see companion doc).
2. APScheduler worker if you want real scheduled delivery.
3. Operator dashboard for the `pattern_gap` review queue (plan §3.6).
