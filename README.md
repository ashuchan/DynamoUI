# DynamoUI

LLM-powered UI generation for backend data models. Define your database schema as YAML skill files — DynamoUI serves query, filter, and mutation interfaces with no frontend code required.

## Tech Stack

- **Python 3.11+** — FastAPI, Pydantic v2, SQLAlchemy 2, asyncpg
- **PostgreSQL** — primary adapter (read/write user separation)
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

## Architecture

```
backend/
  adapters/               SQLAlchemy adapter layer (PostgreSQL)
    postgresql/           QueryTranslator — joins, aggregations, TOP N
  pattern_cache/          Fuzzy trigger matching, pattern caching, pattern promotion
    promotion/            PatternPromoter — auto-write or review-queue LLM patterns
  skill_registry/         Skill/enum YAML loading, validation, LLM formatting
    llm/                  LLM provider abstraction, QuerySynthesiser, PatternSeeder
    cli/                  Click CLI entry points
    config/               Pydantic Settings (DYNAMO_PG_*, DYNAMO_SKILL_*, DYNAMO_CACHE_*, DYNAMO_LLM_*)
skills/                   *.skill.yaml + *.patterns.yaml — entity definitions
enums/                    *.enum.yaml — enum definitions
pattern_reviews/          Candidate patterns awaiting operator review
tests/                    pytest test suite
```

## License

MIT — see [LICENSE](LICENSE).
