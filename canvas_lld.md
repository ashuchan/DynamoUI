# DynamoUI — LLD 9: Canvas (Conversational UI Generator)

*Low-Level Design Document · Phase 2 (Canvas) · v1.0 · Initial draft*

---

## 1. Document Context

**Parent PRD:** DynamoUI — Adaptive Data Interface Framework (v1.6)
**Phase:** Canvas — standalone feature track, layered on top of Phase 1 Foundation
**Scope:** Design the DynamoUI Canvas system — a conversational UI generation pipeline that takes user input (domain, theme preferences, data character, operation profile) and produces enriched skill YAMLs, a compiled theme CSS file, a layout config, and domain-seeded NL patterns. Canvas is a post-scaffold enrichment layer, not a replacement for the existing scaffold CLI.
**Dependencies:**
- LLD 1 (Skill YAML Schema + Validator CLI — Canvas output must pass existing validation)
- LLD 4 (Pattern Cache — Canvas seeds domain-aware patterns into the cache)
- LLD 7 (Theming — Canvas writes valid `--dui-*` CSS token files, validated by existing CI script)
- LLD 6 (Table + Detail Card — Canvas enriches `display_hint` and `widget_type` fields consumed by these components)
- LLD 5 (Intent Resolver — domain-seeded patterns reduce LLM fallback rate from day one)

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic v2 / Anthropic SDK / React 18 / TypeScript / Tailwind CSS / Vite

**Architecture:** Canvas is a module within the single DynamoUI FastAPI application. It does not introduce any new services. The Canvas API is consumed by a new Canvas UI (split-pane chat + live preview), itself built using DynamoUI's existing component system.

---

## 2. Design Goals and Non-Goals

**Design Goals:**

- **Config-only output** — Canvas never generates React components or Python code. It generates theme CSS files, enriched YAML diffs, and layout config YAML. The existing runtime consumes these with zero changes.
- **Scaffold-compatible** — Canvas is designed to run *after* `dynamoui scaffold`. It takes the bare scaffold output (with TODO placeholders) and enriches it. It can also run without a prior scaffold (guided mode produces a full skill YAML from scratch).
- **Conversational elicitation** — A multi-turn LLM-driven chat extracts domain intent, operation profile, aesthetic mood, and field priorities. No forms. The operator types naturally.
- **Domain-aware pattern seeding** — Canvas calls the existing `PatternSeeder` with domain context, producing NL trigger patterns appropriate to the operator's industry vertical. This reduces LLM fallback rate from day one.
- **Live preview** — The Canvas UI renders a live preview of the generated theme and layout using the existing DataTable, DetailCard, and NL bar components with synthetic data derived from the skill YAML.
- **Idempotent and reviewable** — All Canvas outputs are YAML and CSS files, committed to the repository. Re-running Canvas on the same input produces the same output (LLM calls use temperature=0).
- **Passes existing validation** — Every Canvas-generated skill YAML must pass `dynamoui validate`. Every theme CSS must pass `validate_theme.py`. Canvas never bypasses these gates.

**Non-Goals:**

- Generating React component code (v3+)
- Per-entity theming (explicitly v2 per LLD 7)
- Runtime theme switching without reload (explicitly v2 per LLD 7)
- Multi-database Canvas sessions (one adapter per session)
- Canvas for MongoDB or non-PostgreSQL adapters (v2 after PostgreSQL reference is proven)
- Automated deployment of generated configs (Canvas writes files; the operator reviews and commits)

---

## 3. Approach Selection — Generation Strategy

**Option A — Config-Only Generation with LLM Synthesis (CHOSEN)**

- Pros: Canvas outputs are YAML + CSS files that slot directly into the existing system. The existing validator, theme CI check, and PatternSeeder are reused without modification. Operator has full control — Canvas produces a proposal, the operator reviews and commits. No new runtime paths to maintain. LLM at temperature=0 makes output deterministic for identical inputs.
- Cons: Does not generate visually novel component layouts beyond the defined archetype set. Extending the archetype library requires adding new layout config schemas.
- Verdict: Best fit. Maximises reuse of existing infrastructure and keeps Canvas outputs inspectable and version-controllable.

**Option B — Code Generation (React Components)**

- Pros: Unlimited visual flexibility.
- Cons: Generated code is a maintenance liability. Every generated component diverges from the DynamoUI component system. Breaks schema-driven rendering. Conflicts with the headless DataTable + theming architecture. Rejected outright.

**Option C — Runtime Dynamic Layout**

- Pros: No file writing; config applied at runtime.
- Cons: Requires a new runtime layout engine. State management complexity. Cannot be validated at build time. Out of scope for a config-first framework.
- Verdict: Rejected.

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    DynamoUI FastAPI Application                  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    Canvas Module                          │   │
│  │                                                          │   │
│  │  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐ │   │
│  │  │ Conversation │──▶│  CanvasIntent│──▶│ThemeSynth    │ │   │
│  │  │   Driver     │   │  (Pydantic)  │   │              │ │   │
│  │  └─────────────┘   └──────────────┘   └──────┬───────┘ │   │
│  │                           │                   │         │   │
│  │                           ▼                   ▼         │   │
│  │                    ┌──────────────┐   theme-{name}.css  │   │
│  │                    │LayoutSynth   │   (→ existing       │   │
│  │                    │              │    validate_theme)   │   │
│  │                    └──────┬───────┘                     │   │
│  │                           │                             │   │
│  │                           ▼                             │   │
│  │                    layout.config.yaml                   │   │
│  │                           │                             │   │
│  │                    ┌──────▼───────┐                     │   │
│  │                    │SkillEnricher │                     │   │
│  │                    │              │                     │   │
│  │                    └──────┬───────┘                     │   │
│  │                           │                             │   │
│  │            ┌──────────────┼──────────────┐              │   │
│  │            ▼              ▼              ▼              │   │
│  │   enriched .skill.yaml  .patterns.yaml  .mutations.yaml │   │
│  │   (→ existing dynamoui validate)                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  Existing modules consumed (unmodified):                         │
│    SkillRegistry · PatternCache · PatternSeeder                  │
│    validate_theme.py · dynamoui validate                         │
└─────────────────────────────────────────────────────────────────┘
```

**Canvas API endpoints (new):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/canvas/session` | Start a Canvas session, returns `{ session_id }` |
| `GET`  | `/api/v1/canvas/session/{id}` | Get full session (messages + partial_intent + state) for FE rehydration |
| `POST` | `/api/v1/canvas/session/{id}/message` | Send a chat turn, returns `{ reply, intent_update, session_state }` |
| `GET`  | `/api/v1/canvas/session/{id}/intent` | Get current intent envelope `{ intent, state }` (polled by FE every 2s while eliciting) |
| `POST` | `/api/v1/canvas/session/{id}/generate` | Trigger generation pipeline from finalised intent (no body — uses session state) |
| `GET`  | `/api/v1/canvas/session/{id}/preview` | Get synthetic preview data for live Canvas UI (theme CSS inlined) |
| `GET`  | `/api/v1/canvas/session/{id}/artifacts` | Download generated files as a zip (cookie-authenticated for `<a download>`) |

All endpoints are cookie-authenticated (the same session cookie used elsewhere in the app). The `GET /artifacts` route in particular relies on cookie auth because browsers do not send `Authorization: Bearer` on plain `<a href download>` links.

---

## 5. Core Data Models

### 5.1 CanvasIntent

The structured output of the conversation. All downstream synthesisers consume this.

```python
from pydantic import BaseModel
from enum import Enum
from typing import Optional

class Domain(str, Enum):
    FINTECH = "fintech"
    LOGISTICS = "logistics"
    HR = "hr"
    SAAS_B2B = "saas_b2b"
    HEALTHCARE = "healthcare"
    ECOMMERCE = "ecommerce"
    LEGAL = "legal"
    EDUCATION = "education"
    MANUFACTURING = "manufacturing"
    GENERIC = "generic"

class AestheticMood(str, Enum):
    ENTERPRISE = "enterprise"      # Dense, monochrome, data-forward
    FUNCTIONAL = "functional"      # Utility-first, minimal chrome
    MODERN_SAAS = "modern_saas"    # Clean, indigo/violet, rounded
    FRIENDLY = "friendly"          # Warm colours, generous spacing
    CLINICAL = "clinical"          # Teal/blue, structured, high contrast
    BOLD_CONSUMER = "bold_consumer" # High chroma, rounded, expressive

class OperationProfile(str, Enum):
    READ_HEAVY = "read_heavy"         # Dashboards, monitoring
    WRITE_HEAVY = "write_heavy"       # Data entry, forms
    REVIEW_AUDIT = "review_audit"     # Approval workflows
    MIXED = "mixed"                   # Balanced read/write

class DensityPreference(str, Enum):
    COMPACT = "compact"       # 32px rows, tight spacing
    STANDARD = "standard"     # 44px rows, standard spacing
    COMFORTABLE = "comfortable" # 56px rows, generous spacing

class CanvasIntent(BaseModel):
    session_id: str
    domain: Domain
    aesthetic_mood: AestheticMood
    operation_profile: OperationProfile
    density: DensityPreference
    primary_entity: str                    # Most important entity from skill YAML
    entity_priorities: list[str]           # Ordered list of entity names
    key_status_fields: list[str]           # Fields that should render as badges
    key_monetary_fields: list[str]         # Fields with currency formatting
    key_datetime_fields: list[str]         # Fields with date/relative formatting
    enable_kanban: bool = False            # True if status enum is orderable
    enable_timeline: bool = False          # True if entity has created_at + status
    custom_theme_name: str                 # e.g. "slate-pro", "violet-soft"
    operator_notes: str = ""               # Free-text captured from conversation
```

### 5.2 ThemeManifest

Internal representation before writing to CSS.

```python
class ThemeManifest(BaseModel):
    name: str
    primary: str
    primary_hover: str
    primary_foreground: str
    surface: str
    surface_secondary: str
    surface_tertiary: str
    text: str
    text_secondary: str
    text_muted: str
    border: str
    border_strong: str
    success: str
    warning: str
    error: str
    info: str
    font_sans: str
    font_mono: str
    radius_sm: str
    radius_md: str
    radius_lg: str
    # Dark mode overrides (same fields, suffixed _dark)
    primary_dark: str
    surface_dark: str
    surface_secondary_dark: str
    surface_tertiary_dark: str
    text_dark: str
    text_secondary_dark: str
    border_dark: str
    border_strong_dark: str
```

### 5.3 LayoutConfig

Written to `layout.config.yaml`, consumed by the frontend shell.

```python
class LayoutArchetype(str, Enum):
    DASHBOARD = "dashboard"       # Metric cards + primary table + sidebar filters
    DATA_ENTRY = "data_entry"     # Split pane: list left, form right
    REVIEW_AUDIT = "review_audit" # Full-width table, status prominent, bulk actions
    KANBAN = "kanban"             # Columns = enum values of status field
    TIMELINE = "timeline"         # Temporal progression view

class NavStyle(str, Enum):
    SIDEBAR = "sidebar"
    TOP_NAV = "top_nav"

class LayoutConfig(BaseModel):
    archetype: LayoutArchetype
    nav_style: NavStyle
    primary_entity: str
    sidebar_entities: list[str]    # Secondary entities in nav
    metric_card_fields: list[str]  # Fields to show as summary metrics
    bulk_action_enabled: bool
    export_enabled: bool
    density: DensityPreference
```

---

## 6. The Conversation Driver

Multi-turn LLM-driven elicitation. Uses the existing LLM abstraction (Anthropic/Google via `DYNAMO_LLM_PROVIDER`).

### 6.1 Elicitation Topics (ordered)

The conversation covers these topics in sequence, but the driver can reorder based on context clues in early turns:

1. **Domain** — "What kind of business or workflow is this tool for?"
2. **Primary data** — "What's the main thing this tool tracks? (e.g. orders, employees, tickets)"
3. **Operation profile** — "Will users mostly be viewing data, entering it, or reviewing/approving?"
4. **Aesthetic mood** — "What feel should the interface have? (e.g. clean and corporate, warm and friendly, minimal and functional)"
5. **Density** — "Will users be power users who want dense data, or occasional users who prefer breathing room?"
6. **Key field types** — Inferred from skill YAML if available; confirmed conversationally if not. "Do you have status fields? Monetary amounts? Dates?"

### 6.2 System Prompt

```python
CANVAS_SYSTEM_PROMPT = """
You are DynamoUI Canvas, a design configuration assistant for an adaptive data UI framework.
Your job is to have a friendly, focused conversation to understand what kind of UI the operator 
wants to build — then produce a structured CanvasIntent JSON object.

Rules:
- Ask ONE question at a time. Never ask two questions in one message.
- Be concrete and give 2–3 examples in your questions to make choices tangible.
- Once you have enough information, output ONLY a JSON object with key "intent_update" 
  containing partial CanvasIntent fields you've determined so far.
- When all required fields are populated, output a JSON object with key "intent_complete": true
  alongside the full intent.
- Never invent database fields or entities — only reference what's in the provided skill YAML context.
- Respond only in JSON when updating intent. Use plain text for questions.

Skill YAML context (if available):
{skill_yaml_context}
"""
```

### 6.3 Conversation State Machine

```python
class ConversationState(str, Enum):
    ELICITING = "eliciting"
    CONFIRMING = "confirming"
    COMPLETE = "complete"

class CanvasSession(BaseModel):
    session_id: str
    state: ConversationState = ConversationState.ELICITING
    messages: list[dict]  # OpenAI-style message history
    partial_intent: dict  # Accumulated intent fields
    skill_yaml_context: str = ""  # Injected from existing skill files if present
    created_at: datetime
    updated_at: datetime
```

Sessions are stored in the existing DynamoUI Postgres database (a DynamoUI-owned table, managed by Alembic).

---

## 7. Theme Synthesiser

Maps `CanvasIntent` → `ThemeManifest` → `theme-{name}.css`.

### 7.1 Preset Library

Six canonical presets aligned to domain + mood combinations. Each preset is a full `ThemeManifest`. Custom moods blend from the nearest preset.

```python
THEME_PRESETS: dict[AestheticMood, ThemeManifest] = {
    AestheticMood.ENTERPRISE: ThemeManifest(
        name="slate-pro",
        primary="#1e40af",        primary_hover="#1d3f9e",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f8fafc",
        surface_tertiary="#f1f5f9",
        text="#0f172a",           text_secondary="#475569",
        text_muted="#94a3b8",
        border="#e2e8f0",         border_strong="#cbd5e1",
        success="#15803d",        warning="#b45309",
        error="#b91c1c",          info="#1e40af",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.125rem",     radius_md="0.25rem",  radius_lg="0.375rem",
        # dark overrides
        primary_dark="#3b82f6",   surface_dark="#0f172a",
        surface_secondary_dark="#1e293b", surface_tertiary_dark="#334155",
        text_dark="#f1f5f9",      text_secondary_dark="#94a3b8",
        border_dark="#334155",    border_strong_dark="#475569",
    ),
    AestheticMood.MODERN_SAAS: ThemeManifest(
        name="indigo-saas",
        primary="#4f46e5",        primary_hover="#4338ca",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f9fafb",
        surface_tertiary="#f3f4f6",
        text="#111827",           text_secondary="#6b7280",
        text_muted="#9ca3af",
        border="#e5e7eb",         border_strong="#d1d5db",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#4f46e5",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.25rem",      radius_md="0.375rem", radius_lg="0.5rem",
        primary_dark="#6366f1",   surface_dark="#111827",
        surface_secondary_dark="#1f2937", surface_tertiary_dark="#374151",
        text_dark="#f9fafb",      text_secondary_dark="#9ca3af",
        border_dark="#374151",    border_strong_dark="#4b5563",
    ),
    AestheticMood.FUNCTIONAL: ThemeManifest(
        name="zinc-ops",
        primary="#3f3f46",        primary_hover="#27272a",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#fafafa",
        surface_tertiary="#f4f4f5",
        text="#18181b",           text_secondary="#52525b",
        text_muted="#a1a1aa",
        border="#e4e4e7",         border_strong="#d4d4d8",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#3f3f46",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.125rem",     radius_md="0.25rem",  radius_lg="0.25rem",
        primary_dark="#71717a",   surface_dark="#18181b",
        surface_secondary_dark="#27272a", surface_tertiary_dark="#3f3f46",
        text_dark="#fafafa",      text_secondary_dark="#a1a1aa",
        border_dark="#3f3f46",    border_strong_dark="#52525b",
    ),
    AestheticMood.FRIENDLY: ThemeManifest(
        name="violet-soft",
        primary="#7c3aed",        primary_hover="#6d28d9",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#faf5ff",
        surface_tertiary="#f3e8ff",
        text="#1c1917",           text_secondary="#57534e",
        text_muted="#a8a29e",
        border="#e9d5ff",         border_strong="#d8b4fe",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#7c3aed",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.375rem",     radius_md="0.5rem",   radius_lg="0.75rem",
        primary_dark="#a78bfa",   surface_dark="#1c1917",
        surface_secondary_dark="#292524", surface_tertiary_dark="#44403c",
        text_dark="#fafaf9",      text_secondary_dark="#a8a29e",
        border_dark="#44403c",    border_strong_dark="#57534e",
    ),
    AestheticMood.CLINICAL: ThemeManifest(
        name="teal-clinical",
        primary="#0f766e",        primary_hover="#0d6663",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f0fdfa",
        surface_tertiary="#ccfbf1",
        text="#134e4a",           text_secondary="#0f766e",
        text_muted="#5eead4",
        border="#99f6e4",         border_strong="#5eead4",
        success="#15803d",        warning="#b45309",
        error="#b91c1c",          info="#0f766e",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.25rem",      radius_md="0.375rem", radius_lg="0.5rem",
        primary_dark="#14b8a6",   surface_dark="#042f2e",
        surface_secondary_dark="#0d3330", surface_tertiary_dark="#134e4a",
        text_dark="#f0fdfa",      text_secondary_dark="#5eead4",
        border_dark="#134e4a",    border_strong_dark="#0f766e",
    ),
    AestheticMood.BOLD_CONSUMER: ThemeManifest(
        name="rose-consumer",
        primary="#e11d48",        primary_hover="#be123c",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#fff1f2",
        surface_tertiary="#ffe4e6",
        text="#0f172a",           text_secondary="#64748b",
        text_muted="#94a3b8",
        border="#fecdd3",         border_strong="#fda4af",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#e11d48",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.375rem",     radius_md="0.5rem",   radius_lg="1rem",
        primary_dark="#fb7185",   surface_dark="#0f0a0a",
        surface_secondary_dark="#1c0a0a", surface_tertiary_dark="#350d0d",
        text_dark="#fff1f2",      text_secondary_dark="#fda4af",
        border_dark="#350d0d",    border_strong_dark="#9f1239",
    ),
}
```

### 7.2 CSS Generation

```python
class ThemeSynthesiser:
    def synthesise(self, intent: CanvasIntent) -> str:
        """Returns the full CSS file content as a string."""
        preset = THEME_PRESETS[intent.aesthetic_mood]
        density_tokens = DENSITY_TOKENS[intent.density]
        return self._render_css(preset, density_tokens, intent.custom_theme_name)

    def _render_css(self, m: ThemeManifest, density: dict, name: str) -> str:
        return f"""/* DynamoUI Theme: {name} — generated by Canvas */
/* DO NOT EDIT — re-run Canvas to regenerate */
:root {{
  --dui-primary: {m.primary};
  --dui-primary-hover: {m.primary_hover};
  --dui-primary-foreground: {m.primary_foreground};
  --dui-surface: {m.surface};
  --dui-surface-secondary: {m.surface_secondary};
  --dui-surface-tertiary: {m.surface_tertiary};
  --dui-text: {m.text};
  --dui-text-secondary: {m.text_secondary};
  --dui-text-muted: {m.text_muted};
  --dui-border: {m.border};
  --dui-border-strong: {m.border_strong};
  --dui-success: {m.success};
  --dui-warning: {m.warning};
  --dui-error: {m.error};
  --dui-info: {m.info};
  --dui-font-sans: {m.font_sans};
  --dui-font-mono: {m.font_mono};
  --dui-radius-sm: {m.radius_sm};
  --dui-radius-md: {m.radius_md};
  --dui-radius-lg: {m.radius_lg};
  /* Density tokens */
  --dui-row-height: {density['row_height']};
  --dui-cell-padding-y: {density['cell_padding_y']};
  --dui-card-padding: {density['card_padding']};
  --dui-section-gap: {density['section_gap']};
}}

@media (prefers-color-scheme: dark) {{
  :root {{
    --dui-primary: {m.primary_dark};
    --dui-surface: {m.surface_dark};
    --dui-surface-secondary: {m.surface_secondary_dark};
    --dui-surface-tertiary: {m.surface_tertiary_dark};
    --dui-text: {m.text_dark};
    --dui-text-secondary: {m.text_secondary_dark};
    --dui-border: {m.border_dark};
    --dui-border-strong: {m.border_strong_dark};
  }}
}}
"""

DENSITY_TOKENS = {
    DensityPreference.COMPACT: {
        "row_height": "2rem",       # 32px
        "cell_padding_y": "0.25rem",
        "card_padding": "0.75rem",
        "section_gap": "0.75rem",
    },
    DensityPreference.STANDARD: {
        "row_height": "2.75rem",    # 44px
        "cell_padding_y": "0.5rem",
        "card_padding": "1rem",
        "section_gap": "1rem",
    },
    DensityPreference.COMFORTABLE: {
        "row_height": "3.5rem",     # 56px
        "cell_padding_y": "0.75rem",
        "card_padding": "1.5rem",
        "section_gap": "1.5rem",
    },
}
```

After writing, the existing `validate_theme.py` CI script is called programmatically. Canvas refuses to write a theme that fails validation.

---

## 8. Layout Synthesiser

Maps `CanvasIntent` → `LayoutConfig` → `layout.config.yaml`.

### 8.1 Archetype Selection Logic

```python
class LayoutSynthesiser:
    def synthesise(self, intent: CanvasIntent) -> LayoutConfig:
        archetype = self._select_archetype(intent)
        nav_style = self._select_nav(intent)
        metric_fields = self._select_metric_fields(intent)
        return LayoutConfig(
            archetype=archetype,
            nav_style=nav_style,
            primary_entity=intent.primary_entity,
            sidebar_entities=intent.entity_priorities[1:4],  # max 3 secondary
            metric_card_fields=metric_fields,
            bulk_action_enabled=intent.operation_profile in [
                OperationProfile.REVIEW_AUDIT, OperationProfile.MIXED
            ],
            export_enabled=intent.operation_profile in [
                OperationProfile.READ_HEAVY, OperationProfile.MIXED
            ],
            density=intent.density,
        )

    def _select_archetype(self, intent: CanvasIntent) -> LayoutArchetype:
        if intent.enable_kanban:
            return LayoutArchetype.KANBAN
        if intent.enable_timeline:
            return LayoutArchetype.TIMELINE
        if intent.operation_profile == OperationProfile.READ_HEAVY:
            return LayoutArchetype.DASHBOARD
        if intent.operation_profile == OperationProfile.WRITE_HEAVY:
            return LayoutArchetype.DATA_ENTRY
        if intent.operation_profile == OperationProfile.REVIEW_AUDIT:
            return LayoutArchetype.REVIEW_AUDIT
        return LayoutArchetype.DASHBOARD  # default

    def _select_nav(self, intent: CanvasIntent) -> NavStyle:
        # Enterprise + functional → sidebar; consumer/friendly → top nav
        return NavStyle.SIDEBAR if intent.aesthetic_mood in [
            AestheticMood.ENTERPRISE, AestheticMood.FUNCTIONAL, AestheticMood.CLINICAL
        ] else NavStyle.TOP_NAV

    def _select_metric_fields(self, intent: CanvasIntent) -> list[str]:
        # Monetary and count fields surface as metric cards in dashboard archetype
        return intent.key_monetary_fields[:4]  # max 4 metric cards
```

### 8.2 Layout Config YAML Format

```yaml
# layout.config.yaml — generated by DynamoUI Canvas
# DO NOT EDIT — re-run Canvas to regenerate
canvas_version: "1.0"
archetype: dashboard
nav_style: sidebar
primary_entity: Order
sidebar_entities:
  - Customer
  - Product
  - Warehouse
metric_card_fields:
  - total_value
  - shipping_cost
bulk_action_enabled: true
export_enabled: true
density: compact
```

---

## 9. Skill Enricher

Post-scaffold pass over skill YAMLs. Adds `display_hint`, `widget_type`, `column_priority`, and `badge_style` — the fields that LLD 1 defines as optional but that `dynamoui scaffold` leaves as TODO placeholders.

### 9.1 Enrichment Rules

```python
class SkillEnricher:
    def enrich(self, skill_yaml: dict, intent: CanvasIntent) -> dict:
        """Returns an enriched copy of the skill YAML dict."""
        enriched = deepcopy(skill_yaml)
        for field in enriched.get("fields", []):
            field.update(self._infer_field_hints(field, intent))
        return enriched

    def _infer_field_hints(self, field: dict, intent: CanvasIntent) -> dict:
        hints = {}
        fname = field["name"]
        ftype = field.get("type", "string")

        # Display hint inference
        if fname in intent.key_status_fields or field.get("enumRef"):
            hints["display_hint"] = "badge"
            hints["badge_style"] = "semantic"   # maps enum → success/warning/error
            hints["widget_type"] = "select"
            hints["column_priority"] = "high"
        elif fname in intent.key_monetary_fields:
            hints["display_hint"] = "currency"
            hints["widget_type"] = "number"
            hints["column_priority"] = "high"
        elif fname in intent.key_datetime_fields or ftype in ("timestamp", "date"):
            hints["display_hint"] = "relative_time"  # "3 days ago"
            hints["widget_type"] = "datepicker"
            hints["column_priority"] = "medium"
        elif ftype == "boolean":
            hints["display_hint"] = "toggle"
            hints["widget_type"] = "checkbox"
            hints["column_priority"] = "low"
        elif ftype == "text" and field.get("isFK"):
            hints["display_hint"] = "link"    # FK drill-down
            hints["widget_type"] = "fk_select"
            hints["column_priority"] = "medium"
        elif fname in ("id", "uuid", "created_at", "updated_at"):
            hints["column_priority"] = "low"  # Hide by default in table
        else:
            hints["display_hint"] = "text"
            hints["widget_type"] = "input"
            hints["column_priority"] = "medium"

        return hints
```

### 9.2 Validated Output

After enrichment, the enriched YAML is passed through the existing Pydantic model validation (`SkillModel.model_validate(enriched)`). Canvas refuses to write a skill YAML that fails this check.

---

## 10. Domain Pattern Seeder Integration

Canvas calls the existing `PatternSeeder` with domain context. The domain library is a new addition — a YAML file per domain containing NL trigger templates with `{entity}` placeholders.

### 10.1 Domain Pattern Library (new file: `canvas/domain_patterns/`)

```yaml
# canvas/domain_patterns/logistics.yaml
domain: logistics
patterns:
  - template: "show all {entity} with status {status}"
    intent: READ
    entity_hint: "Order, Shipment, Warehouse"
  - template: "pending {entity} this week"
    intent: READ
  - template: "overdue {entity}"
    intent: READ
  - template: "assign {entity} to driver"
    intent: MUTATE
  - template: "{entity} by region"
    intent: VISUALIZE
  - template: "bulk update {entity} status to shipped"
    intent: MUTATE
```

### 10.2 Seeder Call

```python
class CanvasDomainPatternSeeder:
    def seed(self, intent: CanvasIntent, skill_registry: SkillRegistry) -> list[Pattern]:
        domain_file = f"canvas/domain_patterns/{intent.domain.value}.yaml"
        templates = yaml.safe_load(open(domain_file))["patterns"]
        seeded = []
        for entity_name in intent.entity_priorities:
            entity = skill_registry.get(entity_name)
            for tmpl in templates:
                nl_trigger = tmpl["template"].replace("{entity}", entity.label_plural)
                # Re-use existing PatternSeeder.from_template()
                pattern = PatternSeeder.from_template(
                    nl_trigger=nl_trigger,
                    entity=entity_name,
                    intent=tmpl["intent"],
                    confidence=0.75,  # Domain patterns start at 0.75, promote on usage
                )
                seeded.append(pattern)
        return seeded
```

---

## 11. Canvas UI (Frontend)

A new route `/canvas` in the DynamoUI frontend. Built using the existing component system — it eats its own dog food.

### 11.1 Layout

```
┌─────────────────────────────────────────────────────────┐
│  DynamoUI Canvas                              [Generate] │
├──────────────────────┬──────────────────────────────────┤
│                      │                                  │
│   Chat Panel         │   Live Preview                   │
│                      │                                  │
│   [Assistant msg]    │   ┌────────────────────────┐     │
│                      │   │ Theme: slate-pro        │     │
│   [User msg]         │   │ Archetype: Dashboard    │     │
│                      │   │                         │     │
│   [Assistant msg]    │   │  [Metric Card] ×4       │     │
│                      │   │                         │     │
│   ──────────────     │   │  [DataTable preview]    │     │
│   [NL input bar]     │   │  with synthetic rows    │     │
│                      │   └────────────────────────┘     │
│                      │                                  │
│                      │   Intent summary:                │
│                      │   Domain: Logistics              │
│                      │   Mood: Functional               │
│                      │   Profile: Review/Audit          │
└──────────────────────┴──────────────────────────────────┘
```

### 11.2 Live Preview

The preview panel renders the existing `DataTable` and `DetailCard` components with:
- The generated `theme-{name}.css` applied inline (injected into a scoped `<div>`)
- Synthetic rows generated from the enriched skill YAML (field names + type-appropriate fake values)
- The layout archetype applied (metric cards shown for dashboard; split pane shown for data-entry)

No new rendering components. The preview is a live instance of the real runtime.

### 11.3 Intent Summary Panel

A real-time structured summary of the current `CanvasIntent` state, updating as the conversation progresses. Shows the operator what Canvas has understood so far, making it easy to correct misunderstandings before generation.

---

## 12. File Output Structure

All Canvas outputs are written to a `canvas-output/` directory at the project root. The operator reviews and commits these files.

```
canvas-output/
├── themes/
│   └── theme-{name}.css          # → copy to src/themes/ and set DYNAMO_THEME_FILE
├── skills/
│   └── {entity}.skill.yaml       # Enriched versions (review diffs before overwriting)
├── patterns/
│   └── {entity}.patterns.yaml    # Domain-seeded patterns
└── layout.config.yaml            # Frontend layout shell config
```

A `canvas-output/README.md` is also generated, explaining each file and the steps to apply them.

---

## 13. Configuration

```python
class CanvasSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='DYNAMO_CANVAS_')

    enabled: bool = True
    llm_model: str = 'claude-haiku-4-5-20251001'   # Fast for conversation turns
    generation_model: str = 'claude-sonnet-4-6'     # Sonnet for enrichment synthesis
    llm_temperature: float = 0.0                    # Deterministic generation
    max_conversation_turns: int = 20    # Configurable cap; HTTP 409 once reached
    session_ttl_hours: int = 48
    output_dir: Path = Path('./canvas-output')
    domain_patterns_dir: Path = Path('./canvas/domain_patterns')
    validate_theme_on_write: bool = True            # Always; cannot be disabled in prod
    validate_skill_on_write: bool = True            # Always; cannot be disabled in prod
```

---

## 14. Security and Privacy

- Canvas sessions are scoped to the authenticated operator. No session data is shared across operators.
- The LLM receives only the skill YAML schema (field names, types, descriptions) — never actual database row data. Operator notes captured in conversation are stored in the session table and are not sent to external LLMs after the conversation completes.
- Canvas-generated theme files are validated at write time by the existing `validate_theme.py` — no user-controlled CSS is injected into the application without passing this gate.
- Canvas-generated skill YAMLs are validated by the existing Pydantic models before being written to disk.
- `DYNAMO_CANVAS_LLM_MODEL` and `DYNAMO_CANVAS_GENERATION_MODEL` are separate env vars — operators can use a cheaper model for conversation and a more capable model for generation.

---

## 15. Testing Strategy

**Unit tests:**
- `ThemeSynthesiser`: For each preset, assert all required `--dui-*` tokens are present and contrast ratios ≥ 4.5:1. Assert density tokens are correctly applied.
- `LayoutSynthesiser`: For each `OperationProfile` × `AestheticMood` combination, assert correct archetype and nav style selected.
- `SkillEnricher`: For each field type (enum, monetary, datetime, boolean, FK, PK), assert correct `display_hint`, `widget_type`, and `column_priority` are set.
- `ConversationDriver`: Mock LLM responses; assert state machine transitions correctly from `ELICITING` → `CONFIRMING` → `COMPLETE`.

**Integration tests:**
- Full pipeline test: Load a fixture skill YAML → run Canvas with a fixture `CanvasIntent` → assert all output files pass existing validators (`dynamoui validate`, `validate_theme.py`).
- Domain pattern seeder: For each domain YAML, assert all generated patterns are valid `Pattern` objects and pass `PatternCache` insertion.

**Golden fixture tests:**
- For each aesthetic mood preset, generate a theme CSS → compare against a committed golden fixture. Catch unintentional token regressions.

**Canvas UI tests (Playwright):**
- Assert chat turn sends message and receives response.
- Assert live preview updates when intent changes.
- Assert "Generate" button is disabled until `intent_complete: true`.
- Assert download zip contains all four output file types.

---

## 16. Delivery Phases

### Phase C1 — Core Pipeline (3 weeks)

**Goal:** End-to-end generation from a `CanvasIntent` object (no conversation UI yet). Operators construct `CanvasIntent` via API directly.

**Deliverables:**
- `CanvasIntent`, `ThemeManifest`, `LayoutConfig` Pydantic models
- `ThemeSynthesiser` with all 6 presets
- `LayoutSynthesiser` with all 5 archetypes
- `SkillEnricher` with all field type rules
- `CanvasDomainPatternSeeder` + domain pattern YAMLs for: logistics, fintech, HR, generic
- Canvas API endpoints: `POST /generate`, `GET /artifacts`
- Output directory structure + `README.md` generator
- Unit + integration tests for all synthesisers
- Existing CI gates called programmatically (theme validation, skill validation)

### Phase C2 — Conversation Driver (2 weeks)

**Goal:** Multi-turn LLM conversation that populates `CanvasIntent`. No UI yet — accessible via API.

**Deliverables:**
- `CanvasSession` model + Alembic migration
- `ConversationDriver` with system prompt and state machine
- Session persistence (Postgres, DynamoUI-owned table)
- Skill YAML context injection from existing `SkillRegistry`
- `POST /session`, `POST /session/{id}/message`, `GET /session/{id}/intent` endpoints
- Conversation state machine tests with mocked LLM

### Phase C3 — Canvas UI (3 weeks)

**Goal:** Split-pane Canvas UI accessible at `/canvas`. Live preview, intent summary, and download.

**Deliverables:**
- `/canvas` route in React frontend
- Chat panel (reuses NL input bar component)
- Live preview panel (scoped theme injection, synthetic data, layout archetype rendering)
- Intent summary panel (real-time `CanvasIntent` display)
- `GET /preview` endpoint (returns synthetic rows from enriched skill YAML)
- Generate + download flow
- Playwright end-to-end tests

### Phase C4 — Domain Expansion + Polish (2 weeks)

**Goal:** Additional domain patterns, operator feedback loop, and Canvas UI polish.

**Deliverables:**
- Domain pattern YAMLs for: SaaS B2B, healthcare, e-commerce, legal, manufacturing
- Canvas session history (list past sessions, re-open and re-generate)
- `canvas-output/README.md` improvements with domain-specific onboarding notes
- Contrast ratio preview in UI (show WCAG pass/fail for generated theme)
- Operator feedback: thumbs up/down on generated output, stored in session table

---

## 17. Claude Code Task Files

The following task files should be created in `tasks/canvas/` for Claude Code implementation.

### Task C1.1 — Canvas Models

**File:** `tasks/canvas/c1.1-canvas-models.md`

```markdown
# Task C1.1 — Canvas Pydantic Models

## Goal
Implement the core Pydantic v2 models for Canvas: CanvasIntent, ThemeManifest, 
LayoutConfig, DensityPreference, and all supporting enums.

## Files to create
- `canvas/models.py` — all Pydantic models and enums defined in LLD 9, §5

## Constraints
- Use Pydantic v2 (model_validator, field_validator, SettingsConfigDict pattern)
- All enums must be str-based (for YAML serialisation)
- CanvasIntent must be JSON-serialisable (for LLM output parsing)
- No circular imports with existing skill models in `skills/models.py`

## Acceptance criteria
- `from canvas.models import CanvasIntent, ThemeManifest, LayoutConfig` works
- All 6 AestheticMood values, 5 OperationProfile values, 10 Domain values present
- `CanvasIntent.model_json_schema()` produces valid JSON Schema
- Tests in `tests/canvas/test_models.py` — test construction and serialisation for 
  every model, including invalid inputs
```

### Task C1.2 — Theme Synthesiser

**File:** `tasks/canvas/c1.2-theme-synthesiser.md`

```markdown
# Task C1.2 — ThemeSynthesiser

## Goal
Implement ThemeSynthesiser and the 6 preset ThemeManifest definitions. 
Output: valid theme CSS that passes the existing validate_theme.py CI script.

## Files to create
- `canvas/theme_synthesiser.py` — ThemeSynthesiser class + THEME_PRESETS dict + 
  DENSITY_TOKENS dict, all as defined in LLD 9, §7

## Files to read first
- `scripts/validate_theme.py` — understand the validation contract
- `src/themes/theme-default.css` — understand the expected CSS format
- `canvas/models.py` (from C1.1)

## Constraints
- ThemeSynthesiser.synthesise() must call validate_theme programmatically and raise
  CanvasValidationError if validation fails — never write an invalid theme to disk
- All 6 presets must produce light + dark mode tokens
- Density tokens are additive — they do NOT override the --dui-* tokens from LLD 7,
  they ADD new --dui-row-height, --dui-cell-padding-y etc. tokens
- Generated CSS must include the "DO NOT EDIT" comment header

## Acceptance criteria
- ThemeSynthesiser.synthesise(intent) returns a valid CSS string for all 6 mood presets
- All generated themes pass validate_theme.py (test this programmatically in the test)
- Contrast ratio ≥ 4.5:1 between --dui-text and --dui-surface for all 6 presets (both modes)
- Tests in `tests/canvas/test_theme_synthesiser.py` — one test per preset, 
  golden fixture comparison for regression detection
```

### Task C1.3 — Layout Synthesiser

**File:** `tasks/canvas/c1.3-layout-synthesiser.md`

```markdown
# Task C1.3 — LayoutSynthesiser

## Goal
Implement LayoutSynthesiser. Input: CanvasIntent. 
Output: LayoutConfig + serialised layout.config.yaml string.

## Files to create
- `canvas/layout_synthesiser.py` — LayoutSynthesiser class as defined in LLD 9, §8

## Files to read first
- `canvas/models.py` (from C1.1)
- `lld/lld-09-canvas.md` §8 for archetype selection logic

## Constraints
- Kanban archetype takes priority over all others when enable_kanban=True
- Timeline archetype takes priority over dashboard/data-entry when enable_timeline=True
- sidebar_entities is capped at 3 (the 3 highest-priority non-primary entities)
- metric_card_fields is capped at 4 (key_monetary_fields[:4])
- YAML output must be valid YAML (use PyYAML for serialisation)
- Include "DO NOT EDIT" comment header in YAML output

## Acceptance criteria
- All 5 archetypes are reachable via the selection logic
- Both NavStyle values are reachable
- LayoutSynthesiser.to_yaml(config) produces valid YAML that round-trips correctly
- Tests in `tests/canvas/test_layout_synthesiser.py` — test all 5 archetypes,
  test sidebar truncation at 3, test metric field truncation at 4
```

### Task C1.4 — Skill Enricher

**File:** `tasks/canvas/c1.4-skill-enricher.md`

```markdown
# Task C1.4 — SkillEnricher

## Goal
Implement SkillEnricher. Post-scaffold pass that fills in display_hint, widget_type,
column_priority, and badge_style from CanvasIntent context.

## Files to create
- `canvas/skill_enricher.py` — SkillEnricher class as defined in LLD 9, §9

## Files to read first
- `skills/models.py` — understand the Field model and its optional hint fields
- `canvas/models.py` (from C1.1)
- `lld/lld-01-skill-yaml.md` §4 — display_hint allowed values
- `lld/lld-09-canvas.md` §9 — enrichment rules

## Constraints
- SkillEnricher.enrich() must return a dict that passes SkillModel.model_validate()
  — never return an enriched YAML that fails Pydantic validation
- deepcopy the input before enriching — never mutate the input dict
- PK fields (isPK=True) always get column_priority="low" regardless of other rules
- Fields already having display_hint set (not None) must NOT be overwritten 
  (respect existing operator-authored hints)

## Acceptance criteria
- Each of the 7 field type branches (enum/status, monetary, datetime, boolean, FK, PK, default)
  produces the correct hint triple (display_hint, widget_type, column_priority)
- Enriched output passes SkillModel.model_validate() for the Employee, Product, and 
  Order fixture skill YAMLs
- Existing display_hint values are preserved (not overwritten)
- Tests in `tests/canvas/test_skill_enricher.py` — one test per field type branch,
  one integration test with each fixture skill YAML
```

### Task C1.5 — Domain Pattern Seeder + YAMLs

**File:** `tasks/canvas/c1.5-domain-pattern-seeder.md`

```markdown
# Task C1.5 — Domain Pattern Seeder

## Goal
Implement CanvasDomainPatternSeeder and create domain pattern YAML files for
logistics, fintech, HR, and generic domains.

## Files to create
- `canvas/domain_pattern_seeder.py` — CanvasDomainPatternSeeder class (LLD 9, §10)
- `canvas/domain_patterns/logistics.yaml` — 10–15 pattern templates
- `canvas/domain_patterns/fintech.yaml` — 10–15 pattern templates  
- `canvas/domain_patterns/hr.yaml` — 10–15 pattern templates
- `canvas/domain_patterns/generic.yaml` — 8–10 generic pattern templates

## Files to read first
- `patterns/pattern_seeder.py` — understand PatternSeeder.from_template() interface
- `canvas/models.py` (from C1.1)
- `lld/lld-04-pattern-cache.md` — Pattern schema

## Constraints
- Each pattern template must use {entity} placeholder (substituted at seed time)
- Confidence for domain-seeded patterns is 0.75 (below auto-promote threshold of 0.9)
  — they will promote naturally via the existing promotion mechanism
- Domain YAML schema: { domain, patterns: [{template, intent, entity_hint?}] }
- Call PatternSeeder.from_template() — do not directly construct Pattern objects

## Acceptance criteria
- All 4 domain YAMLs load without error
- CanvasDomainPatternSeeder.seed() returns a list of valid Pattern objects for the
  Employee entity using the HR domain
- Generated patterns pass PatternCache.insert() without error
- Tests in `tests/canvas/test_domain_pattern_seeder.py`
```

### Task C1.6 — Canvas API (Generation Endpoints)

**File:** `tasks/canvas/c1.6-canvas-api-generate.md`

```markdown
# Task C1.6 — Canvas Generation API

## Goal
Wire the synthesisers into FastAPI endpoints: POST /generate and GET /artifacts.
POST /generate accepts a CanvasIntent body and runs the full pipeline.
GET /artifacts returns a zip of all generated files.

## Files to create
- `canvas/router.py` — FastAPI router with /generate and /artifacts endpoints
- `canvas/generator.py` — CanvasGenerator orchestrator class that calls all 4 
  synthesisers in sequence and writes output to canvas-output/

## Files to read first
- All canvas/*.py files from C1.1–C1.5
- `main.py` — understand how routers are registered
- `lld/lld-09-canvas.md` §12 — output directory structure

## Constraints
- /generate must be synchronous from the caller's perspective (await all steps)
- If ThemeSynthesiser raises CanvasValidationError, return HTTP 422 with the 
  validation error details — never write partial output
- canvas-output/ directory is created if it does not exist
- /artifacts returns application/zip with Content-Disposition: attachment
- All 4 output file types must be present in the zip: theme CSS, enriched skill YAMLs,
  patterns YAML, layout.config.yaml, README.md
- Register router at prefix /api/v1/canvas in main.py

## Acceptance criteria
- POST /api/v1/canvas/generate with a valid CanvasIntent returns HTTP 200 and 
  { "status": "ok", "files": [...] }
- GET /api/v1/canvas/artifacts returns a valid zip containing all required files
- Theme and skill YAMLs in the zip pass their respective validators
- Integration test: POST generate → GET artifacts → unzip → validate all files
```

### Task C2.1 — Conversation Driver

**File:** `tasks/canvas/c2.1-conversation-driver.md`

```markdown
# Task C2.1 — Conversation Driver

## Goal
Implement the multi-turn LLM conversation driver. Manages session state, sends
turns to the LLM, parses partial/complete CanvasIntent from LLM output.

## Files to create
- `canvas/conversation.py` — ConversationDriver class, CanvasSession model (LLD 9, §6)
- `alembic/versions/{hash}_canvas_sessions.py` — migration for canvas_sessions table

## Files to read first
- `canvas/models.py` (C1.1)
- `intelligence/llm_provider.py` — existing LLM abstraction to reuse
- `lld/lld-09-canvas.md` §6 — system prompt, elicitation topics, state machine
- `lld/lld-05-intent-resolver.md` §5 — LLMFallbackClassifier pattern to follow

## Constraints
- ConversationDriver must use the existing LLM provider abstraction 
  (respect DYNAMO_LLM_PROVIDER env var)
- Use DYNAMO_CANVAS_LLM_MODEL for conversation turns (Haiku by default)
- Session messages stored as JSONB in Postgres canvas_sessions table
- LLM responses containing "intent_update" must be parsed and merged into 
  partial_intent via CanvasIntent.model_validate({**partial, **update})
- If LLM response cannot be parsed as JSON for an intent update, treat it as a 
  plain text assistant message (graceful degradation)
- ConversationState transitions: ELICITING → CONFIRMING (all required fields set) 
  → COMPLETE (operator confirms)
- Temperature must be 0.0 for generation calls; conversation turns may use 0.3

## Acceptance criteria
- POST /session creates a session, returns session_id
- POST /session/{id}/message sends turn, returns assistant reply
- After sufficient turns with mocked LLM, GET /session/{id}/intent returns a 
  complete CanvasIntent
- State machine correctly transitions through all 3 states
- Tests with mocked LLM in `tests/canvas/test_conversation.py`
```

### Task C3.1 — Canvas UI

**File:** `tasks/canvas/c3.1-canvas-ui.md`

```markdown
# Task C3.1 — Canvas UI (React)

## Goal
Implement the /canvas route: split-pane chat + live preview + intent summary + 
download button.

## Files to create
- `src/pages/Canvas.tsx` — main Canvas page component
- `src/components/canvas/ChatPanel.tsx` — conversation chat UI (reuses NL bar)
- `src/components/canvas/PreviewPanel.tsx` — live preview with scoped theme injection
- `src/components/canvas/IntentSummary.tsx` — real-time intent display
- `src/lib/canvasClient.ts` — typed API client for Canvas endpoints

## Files to read first
- `lld/lld-09-canvas.md` §11 — Canvas UI layout
- `src/components/DataTable.tsx` — understand the component API to reuse in preview
- `src/components/NLInputBar.tsx` — reuse for chat panel input
- `lld/lld-07-theming.md` §6 — scoped theme injection approach

## Constraints
- Scoped theme injection: apply generated CSS to a wrapper <div> via a <style> tag 
  scoped with a generated class name — do NOT inject into :root (would affect the 
  whole app)
- Synthetic data for preview: derive from enriched skill YAML field names + types 
  (use the /preview endpoint)
- The Generate button must be disabled until session state === 'complete'
- ChatPanel must scroll to the latest message automatically
- PreviewPanel must re-render when the Canvas session's intent changes (poll 
  /session/{id}/intent every 2s while state === 'eliciting')
- All theme token classes must use dui- prefix (no hardcoded colours in Canvas UI)

## Acceptance criteria
- /canvas route renders without errors
- Sending a chat message displays the assistant reply
- PreviewPanel updates when intent changes (test with mock session)
- Download button triggers /artifacts endpoint and downloads the zip
- Playwright test: full flow — send 3 messages → Generate → download zip
```

---

## 18. Open Questions

All open questions require a decision before Phase C1 begins:

1. **Session storage backend** — Canvas sessions stored in Postgres (DynamoUI-owned table, Alembic-managed). No Redis in Phase C1. Acceptable?

2. **Skill YAML write behaviour** — Canvas writes enriched YAMLs to `canvas-output/skills/`, not directly to `skills/`. The operator manually copies and reviews diffs. Should Canvas also offer a `--apply` flag that copies directly and runs `dynamoui validate` in-place? Recommend: yes, as an explicit opt-in flag.

3. **LLM model for generation vs conversation** — Defaulting to Haiku for conversation (speed) and Sonnet for generation synthesis (quality). Operators may override via env vars. Acceptable?

4. **Domain pattern confidence** — Domain-seeded patterns start at 0.75 confidence (below auto-promote threshold of 0.9). They promote via usage. Should Canvas offer an `--auto-promote` flag to seed at 0.9 directly for known-good domain patterns? Recommend: yes, as an explicit opt-in.

5. **Canvas output versioning** — Should `canvas-output/` include a `canvas-session.json` recording the session_id and intent that produced the output, for auditability? Recommend: yes.