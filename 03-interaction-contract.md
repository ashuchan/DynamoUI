# DynamoUI v2 — Frontend ↔ Backend Interaction Contract

*The wire contract between the two plans. Anything not covered here is ambiguous and must be resolved before code.*

## 0. Contract philosophy

Two non-negotiable rules that shape every endpoint:

1. **Every query execution returns a `provenance` envelope.** Not optional. Not feature-flagged at the frontend's discretion. This is how trust is built into the product.
2. **The frontend never re-runs classification or pattern matching.** The backend owns all query resolution. The frontend holds persisted entities (saved views, dashboards, schedules) and orchestrates UX flows, but it never decides what a user query "means."

These two rules mean the frontend can be stupid and fast, and the backend can evolve its intelligence without breaking clients.

---

## 1. The query execution flow — end to end

This is the central flow. Get it right and the rest falls into place.

### 1.1 User types in the NL bar

```
Frontend                                      Backend
────────                                      ───────

[User types "senior platform engineers"]
  │
  ▼
POST /api/v1/resolve ───────────────────────▶ IntentResolver
  {input: "senior platform engineers"}          │
                                                 │ normalise → classify → pattern match
                                                 │ → candidate = {source: 'cache',
                                                 │                pattern_id: 'Employee.senior_platform',
                                                 │                query_plan: {...}}
                                                 │
                                                 ▼
                                              LLMVerifier.verify(input, candidate)
                                                 │
                                                 │ cache lookup → miss
                                                 │ Haiku call → APPROVE
                                                 │
                                                 ▼
                                              Adapter.execute(query_plan)
                                                 │
                                                 ▼
                                              Assemble ExecutedResult
                                                 │
       ◀────────────────────────────────────────┘
  {
    result: {entity, rows, ...},
    provenance: {
      candidateSource: 'cache',
      patternId: 'Employee.senior_platform',
      patternMatchConfidence: 0.96,
      verifierVerdict: 'approve',
      verifierVerified: true,
      verifierLatencyMs: 42,
      verifierCacheHit: false,
      queryPlan: {...},
      generatedSql: 'SELECT ...',   // if feature-flag on
      executionLatencyMs: 14,
      adapter: 'postgresql',
      skillHash: 'a3f9...',
      llmCostUsd: 0.0003,
      timestamp: '2026-04-17T10:15:00Z',
      sessionId: 'sess_abc123'      // NEW: server-issued session ID for this result
    }
  }

[Frontend navigates to /results/sess_abc123]
[Renders DataTable with provenance badge]
```

### 1.2 When the verifier rejects

```
Backend path on REJECT:

LLMVerifier.verify → verdict: REJECT_PREFER_LLM
  │
  ├─▶ gap_recorder.record(user_input, rejected_candidate, llm_plan, suggestion)
  │
  └─▶ Adapter.execute(verdict.llm_plan)   # the LLM's plan, not the candidate
          │
          ▼
      Result

Response to frontend:
  {
    result: {...},                      # comes from LLM's plan, not candidate
    provenance: {
      candidateSource: 'cache',         # what was originally picked
      patternId: 'Employee.all_engineers',
      patternMatchConfidence: 0.91,     # still a legitimate match
      verifierVerdict: 'reject',        # but verifier disagreed
      verifierVerified: true,
      verifierNote: 'User specified "senior" but pattern does not filter by level',
      verifierLatencyMs: 180,
      reroutedPlan: {...},              # the LLM's plan that actually ran
      originalCandidate: {...},         # for the provenance drawer
      queryPlan: {...},                 # the plan that was actually executed (= reroutedPlan)
      ...
    }
  }
```

The frontend renders a distinct badge (`↪ Rerouted by verifier`) and the drawer shows both plans side by side. **Critical UX detail**: the result shown is always the one that ran. The "candidate vs reroute" story is provenance metadata, not a user choice.

### 1.3 SCHEDULE intent — no execution

```
User types: "email me department headcount weekly"

Frontend                                      Backend
────────                                      ───────

POST /api/v1/resolve
  {input: "email me department headcount weekly"}
                                           ──▶ IntentResolver.classify → SCHEDULE
                                                │
                                                │ Sub-intent extraction: VISUALIZE (chart)
                                                │ Sub-resolution: pattern match or synth
                                                │ Cadence parsing: "weekly" → "0 9 * * MON"
                                                │ Channel parsing: "email me" → current user
                                                │
                                                ▼
                                              LLMVerifier.verify_schedule(
                                                input, subResolution, cadence, channel)
                                                verdict: APPROVE
                                                │
                                                ▼
                                              [Nothing executed yet]

       ◀──────────────────────────────────────┘
  {
    intent: 'SCHEDULE',
    scheduleDraft: {
      sourceType: 'pattern',
      patternId: 'Department.headcount_bar_chart',
      queryPlan: {...},
      vizConfig: {type: 'bar', ...},
      cronExpr: '0 9 * * MON',
      timezone: 'Asia/Kolkata',
      channel: 'email',
      channelConfig: {to: ['alice@corp.com']},
      format: 'html_snapshot',
      nextRuns: [...]           # pre-computed first 5
    },
    provenance: {                # yes, even schedule drafts have provenance
      candidateSource: 'cache',
      verifierVerdict: 'approve',
      ...
    }
  }

[Frontend opens ScheduleModal in confirmation mode]
[User clicks Confirm → POST /api/v1/schedules with scheduleDraft]
```

---

## 2. Endpoint catalog

All new endpoints, grouped by cluster. Existing endpoints from LLD 5/6/8 (resolve, fetchEntityList, fetchSingleRecord, etc.) continue to exist with the provenance envelope change.

### 2.1 Resolution

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/v1/resolve` | `{input, sessionContext?}` | `ResolutionResult` (includes provenance; may be result, schedule draft, alert draft, mutation preview, or clarification request) |
| POST | `/api/v1/resolve/edit` | `{queryPlan}` | `{nlInput: string}` — reverse-translates plan to NL |
| POST | `/api/v1/commands/dispatch` | `{command, args, sessionContext?}` | Same as `/resolve` |
| GET | `/api/v1/search` | `?q&types&limit` | `{results: [{type, id, name, score, ...}]}` |

### 2.2 Saved views

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/v1/views` | `?entity&shared` | `SavedView[]` |
| POST | `/api/v1/views` | `{name, nlInput, queryPlan, entity, resultShape}` | `SavedView` |
| GET | `/api/v1/views/:id` | - | `SavedView` |
| PATCH | `/api/v1/views/:id` | `{name?, isShared?}` | `SavedView` |
| DELETE | `/api/v1/views/:id` | - | 204 |
| POST | `/api/v1/views/:id/execute` | - | `ExecutedResult` (stale flag may be present) |

### 2.3 Dashboards

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/v1/dashboards` | - | `Dashboard[]` |
| POST | `/api/v1/dashboards` | `{name, description?, layout}` | `Dashboard` |
| GET | `/api/v1/dashboards/:id` | - | `DashboardTree` (dashboard + tiles + resolved display configs) |
| PATCH | `/api/v1/dashboards/:id` | `{name?, layout?}` | `Dashboard` |
| DELETE | `/api/v1/dashboards/:id` | - | 204 |
| POST | `/api/v1/dashboards/:id/tiles` | `{sourceType, sourceId, positionX, positionY, width, height, overrides?}` | `Tile` |
| PATCH | `/api/v1/dashboards/:id/tiles/:tileId` | `{positionX?, positionY?, width?, height?, overrides?}` | `Tile` |
| DELETE | `/api/v1/dashboards/:id/tiles/:tileId` | - | 204 |

### 2.4 Home and pins

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/v1/home` | - | `HomeComposition` (pins + default dashboard summary + recent queries + upcoming schedules) |
| POST | `/api/v1/pins` | `{sourceType, sourceId}` | `Pin` |
| DELETE | `/api/v1/pins/:id` | - | 204 |

### 2.5 Schedules

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/v1/schedules` | - | `Schedule[]` |
| POST | `/api/v1/schedules` | `CreateScheduleInput` | `Schedule` |
| GET | `/api/v1/schedules/:id` | - | `Schedule` (with `nextRuns: string[]`) |
| PATCH | `/api/v1/schedules/:id` | `{cronExpr?, enabled?, channelConfig?, ...}` | `Schedule` |
| DELETE | `/api/v1/schedules/:id` | - | 204 |
| POST | `/api/v1/schedules/:id/test` | - | `DeliveryRun` |
| GET | `/api/v1/schedules/:id/runs` | `?limit&before` | `{runs: DeliveryRun[], nextCursor}` |

### 2.6 Alerts

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/v1/alerts` | - | `Alert[]` |
| POST | `/api/v1/alerts` | `CreateAlertInput` or `{nlDescription, savedViewId}` | `Alert` |
| GET | `/api/v1/alerts/:id` | - | `Alert` |
| PATCH | `/api/v1/alerts/:id` | `{condition?, checkCron?, enabled?, ...}` | `Alert` |
| DELETE | `/api/v1/alerts/:id` | - | 204 |
| GET | `/api/v1/alerts/:id/triggers` | `?limit&before` | `{triggers: TriggerEvent[], nextCursor}` |

### 2.7 Patterns (user-facing)

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/v1/patterns/propose` | `{queryPlan, userInput}` | `{proposalId, status: 'queued'}` |

### 2.8 Sharing

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| POST | `/api/v1/share-tokens` | `{sourceType, sourceId, expiresInSeconds?, maxAccessCount?}` | `{token, url, embedUrl}` | Yes |
| GET | `/api/v1/share-tokens` | `?sourceType&sourceId` | `ShareToken[]` | Yes |
| DELETE | `/api/v1/share-tokens/:id` | - | 204 | Yes |
| GET | `/api/v1/shared/:token` | - | `ExecutedResult` (with shared context) | **No** |
| GET | `/embed/:token` | - | HTML page | **No** |

---

## 3. Type definitions (shared source of truth)

These types are the wire contract. Generate them from a single source — recommended: Pydantic models in backend, with `datamodel-code-generator` producing TypeScript `.d.ts` files shipped into the frontend repo as a submodule or published package.

### 3.1 Core envelope

```ts
interface ExecutedResult {
  result: QueryResult;          // existing LLD 6 shape — entity, rows, totalCount, fields, queryTimeMs
  provenance: Provenance;
  sessionId: string;            // server-issued ID; frontend uses for /results/:sessionId
}

interface Provenance {
  candidateSource: 'cache' | 'template' | 'synthesised' | 'saved_view';
  patternId?: string;
  patternMatchConfidence?: number;   // 0-1, only for cache/template

  verifierVerdict: 'approve' | 'reject' | 'approve_with_note' | 'skipped' | 'error';
  verifierVerified: boolean;
  verifierLatencyMs?: number;
  verifierCacheHit?: boolean;
  verifierNote?: string;             // human-readable reason, shown in drawer
  reroutedPlan?: QueryPlan;          // present only if verdict === 'reject'
  originalCandidate?: QueryPlan;     // present only if verdict === 'reject'

  synthesised: boolean;
  synthesisConfidence?: number;      // present only if candidateSource === 'synthesised'

  queryPlan: QueryPlan;              // the plan that actually executed
  generatedSql?: string;             // feature-flagged per deployment

  executionLatencyMs: number;
  adapter: string;                   // 'postgresql' for now
  skillHash: string;
  llmCostUsd: number;
  timestamp: string;                 // ISO 8601
}
```

### 3.2 Resolution responses (discriminated union)

```ts
type ResolutionResult =
  | {kind: 'executed'; executed: ExecutedResult}
  | {kind: 'schedule_draft'; draft: ScheduleDraft; provenance: Provenance}
  | {kind: 'alert_draft'; draft: AlertDraft; provenance: Provenance}
  | {kind: 'mutation_preview'; preview: DiffPreview; provenance: Provenance}
  | {kind: 'clarification_needed'; question: string; candidates: QueryPlan[]};
```

The `clarification_needed` kind is for the case where multiple patterns match with similar scores and the system asks the user to disambiguate — a useful UX escape hatch to have from day one even if the backend rarely uses it initially.

### 3.3 Saved views and dashboards

```ts
interface SavedView {
  id: string;
  ownerUserId: string;
  name: string;
  nlInput: string;
  queryPlan: QueryPlan;
  entity: string;
  resultShape: 'list' | 'single' | 'aggregate' | 'chart';
  isShared: boolean;
  patternIdHint?: string;
  skillHash: string;
  stale: boolean;                    // set by backend if skillHash mismatch detected
  createdAt: string;
  updatedAt: string;
}

interface Dashboard {
  id: string;
  ownerUserId: string;
  name: string;
  description?: string;
  isDefault: boolean;
  layout: DashboardLayout;
  createdAt: string;
  updatedAt: string;
}

interface DashboardLayout {
  grid: '12col';
  tiles: Array<{tileId: string; x: number; y: number; w: number; h: number}>;
}

interface DashboardTree {
  dashboard: Dashboard;
  tiles: DashboardTile[];
  resolvedDisplayConfigs: Record<string, DisplayConfig>;   // by entity name
}

interface DashboardTile {
  id: string;
  dashboardId: string;
  sourceType: 'saved_view' | 'widget' | 'pattern_result';
  sourceId: string;
  position: {x: number; y: number; w: number; h: number};
  overrides?: {title?: string; refreshIntervalSeconds?: number};
}
```

### 3.4 Schedules and alerts

```ts
interface CreateScheduleInput {
  sourceType: 'saved_view' | 'dashboard';
  sourceId: string;
  cronExpr: string;
  timezone: string;
  channel: 'email' | 'slack' | 'webhook';
  channelConfig: EmailConfig | SlackConfig | WebhookConfig;
  format: 'csv' | 'xlsx' | 'html_snapshot' | 'pdf';
}

interface Schedule extends CreateScheduleInput {
  id: string;
  ownerUserId: string;
  enabled: boolean;
  lastRunAt?: string;
  nextRunAt?: string;
  nextRuns: string[];                // next 5 computed
  failureCount: number;
  createdAt: string;
  updatedAt: string;
}

interface DeliveryRun {
  id: string;
  scheduleId?: string;
  alertId?: string;
  startedAt: string;
  finishedAt?: string;
  status: 'running' | 'success' | 'failed';
  rowsDelivered?: number;
  latencyMs?: number;
  errorText?: string;
}

interface ScheduleDraft extends Omit<CreateScheduleInput, 'sourceId'> {
  // When produced by NL resolve, the source isn't saved yet
  sourceSnapshot: {
    queryPlan: QueryPlan;
    resultShape: 'list' | 'single' | 'aggregate' | 'chart';
    vizConfig?: VizConfig;
    suggestedName: string;
  };
  nextRuns: string[];
}

interface Alert {
  id: string;
  ownerUserId: string;
  savedViewId: string;
  condition: AlertCondition;
  checkCron: string;
  channel: string;
  channelConfig: unknown;
  enabled: boolean;
  lastCheckAt?: string;
  lastTriggeredAt?: string;
}

type AlertCondition =
  | {type: 'row_count'; operator: CompareOp; value: number}
  | {type: 'any_row_field'; field: string; operator: CompareOp; value: unknown}
  | {type: 'aggregate'; aggregate: 'sum' | 'avg' | 'min' | 'max'; field: string; operator: CompareOp; value: number};

type CompareOp = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte';
```

---

## 4. Error handling contract

### 4.1 Error envelope

All error responses use:

```json
{
  "error": {
    "code": "stable_snake_case_code",
    "message": "Human readable for display",
    "details": { ...context specific to the code... },
    "traceId": "optional for debugging"
  }
}
```

### 4.2 Stable error codes

| HTTP | Code | Meaning | Frontend handling |
|---|---|---|---|
| 400 | `invalid_query_plan` | Plan failed adapter validation | Show raw error in provenance drawer |
| 400 | `invalid_cron_expression` | Cron unparseable | Inline validation in schedule modal |
| 400 | `invalid_dashboard_layout` | Tile overlap or out of bounds | Refuse PATCH, keep client state |
| 401 | `unauthorized` | No auth | Redirect to login |
| 403 | `forbidden` | Auth'd but no permission | Toast + log |
| 404 | `not_found` | ID doesn't exist | Graceful empty state |
| 409 | `stale_saved_view` | Skill hash mismatch on save | Show "Re-confirm" UX |
| 409 | `name_conflict` | Duplicate name in user's namespace | Inline validation |
| 422 | `verifier_rejected` | Verifier rejected AND the LLM couldn't produce a valid alternative | Show user the verifier's reasoning + suggestion |
| 429 | `rate_limited` | Hitting a quota | Toast with retry-after |
| 429 | `verifier_budget_exceeded` | Per-tenant monthly LLM verifier budget hit | Toast; resolution proceeds WITHOUT verification (degraded) |
| 500 | `adapter_error` | Postgres-side failure | Toast + log + provenance drawer shows details |
| 500 | `llm_provider_error` | Anthropic/Gemini unreachable | Graceful degradation per `on_llm_failure` setting |
| 504 | `llm_timeout` | LLM exceeded `llm_timeout_ms` | Same as `llm_provider_error` |

### 4.3 Degradation modes

Critical behaviour: **the frontend never treats verifier failure as a query failure**. If `verifierVerdict === 'skipped'` or `'error'`, the query result still ran — the user sees data, and the provenance badge shows `⚡ Unverified` instead of `✓ Verified`. The product continues to function when the LLM is down.

---

## 5. Real-time / push concerns

Phase 2 has no WebSocket / SSE. All updates are pull-based via React Query refetch. Specifically:

- Dashboard tiles auto-refresh at `overrides.refreshIntervalSeconds` if set (client-side timer, `refetchInterval` on the React Query hook). No server push.
- Schedule/alert status visible only when the user navigates to the schedule detail page (pull on mount).
- Shared links and embeds do not live-update; they show data as of the moment they were loaded.

Phase 3/4 may add SSE for verifier verdict streaming (so the frontend can show "verifying..." state while results are being prepared) — that's a future optimisation, not a launch requirement.

---

## 6. Caching and invalidation

### 6.1 Backend cache surfaces

- **Pattern cache**: in-process, hot-reloaded by `PatternPromoter` callback on successful promotion.
- **Verdict cache**: in-process LRU (Redis in Phase 4). Key: `sha256(input + plan + skillHash)`.
- **Saved view execute cache**: not cached server-side in v2; each execute re-runs the plan. Client caches via React Query for 60s.

### 6.2 Frontend cache surfaces

- React Query keys as listed in the frontend plan. Stale time: 30s for lists, 60s for individual resource fetches, infinite for `fieldMeta`/`displayConfig` (invalidated only on skill hash change).
- Skill hash is returned in every `provenance` envelope. Frontend compares against a stored `lastKnownSkillHash` in Zustand; on change, invalidates all cached field meta and display configs.

---

## 7. Pagination

All list endpoints use cursor pagination:

```
GET /api/v1/schedules/:id/runs?limit=50&before=2026-04-17T10:00:00Z

Response:
{
  "runs": [...],
  "nextCursor": "2026-04-17T08:30:00Z"    // pass as ?before= next call
}
```

The adapter's existing `QueryPlan.offset/limit` remains for *data* queries against the customer's tables. Metadata list endpoints (schedules, alerts, views) use cursor pagination exclusively.

---

## 8. Auth and authorization

### 8.1 Transport

JWT in Authorization header for authenticated endpoints. Shared links and embeds are the only unauthenticated endpoints; they authorize via the token in the URL.

### 8.2 Ownership rules

- Saved views, dashboards, pins, schedules, alerts: owner-only write. Read: owner unless `isShared`.
- Share tokens: only the token creator can revoke.
- Patterns (propose): any authenticated user can propose; only operators can promote.

### 8.3 Multi-tenancy

Out of scope for v2 (per existing PRD non-goals). All users share one tenant context. When multi-tenancy arrives in v3, the `ownerUserId` column becomes `(tenantId, ownerUserId)` with row-level security policies — additive change, no breaking API shape.

---

## 9. Observability — shared fields

Every request emits a log entry. The frontend-backend agreement on structured fields:

| Field | Source |
|---|---|
| `requestId` | Frontend generates per request; backend echoes in response headers |
| `sessionId` | Backend issues per execution; frontend holds for the result's lifetime |
| `userId` | Backend, from auth context |
| `route` | HTTP path |
| `latencyMs` | Backend, wall-clock |
| `verifierVerdict` | Backend, only on `/resolve` and execute endpoints |
| `candidateSource` | Backend, same surfaces |
| `clientError` | Frontend posts to `/api/v1/logs/frontend-error` if a render crashes; backend logs with same requestId |

This lets you trace a single user query from NL input through verifier, adapter, render, and back.

---

## 10. Versioning

The API is versioned at `/api/v1/`. When v2 arrives (breaking changes), both versions will coexist for a deprecation window. Frontend pins to `/api/v1/` explicitly; backend returns a `X-API-Deprecation: v1 sunset 2027-01-01` header when v2 ships.

Additive fields in responses are not breaking — frontend must ignore unknown fields. Removing fields or changing types is breaking and requires v2.
