# DynamoUI

LLM-powered UI generation for backend data models. Define your database schema as YAML skill files — DynamoUI serves query, filter, and mutation interfaces with no frontend code required.

## Tech Stack

- **Python 3.11+** — FastAPI, Pydantic v2, SQLAlchemy 2, asyncpg
- **PostgreSQL** — primary adapter (read/write user separation)
- **Claude API** — pattern matching and LLM-driven query generation

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

To write to a custom path:

```bash
dynamoui setup --env-file /etc/dynamoui/.env
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

## Scaffolding a schema

Once your connection is configured, use `dynamoui scaffold` to generate skill YAML from your existing PostgreSQL tables. You do not need to write skill files by hand.

### Scaffold a single table

```bash
dynamoui scaffold --adapter postgresql --table employees --output ./skills/employee.skill.yaml
```

### Scaffold all tables in a schema

```bash
dynamoui scaffold --adapter postgresql --schema public --output-dir ./skills/
```

This reflects every table in the `public` schema and writes one `<table>.skill.yaml` per table. Each file is annotated with `# TODO:` markers for fields that need manual review (sensitive columns, enum refs, display config).

### Preview without writing

```bash
dynamoui scaffold --adapter postgresql --table employees --dry-run
```

## CLI reference

```
dynamoui setup              Interactive PostgreSQL onboarding — writes .env
dynamoui validate           Run the 4-phase validation pipeline against skill files
dynamoui scaffold           Generate skill YAML from a live PostgreSQL table or schema
dynamoui compile-patterns   Recompute skill_hash headers in all *.patterns.yaml files
```

### `dynamoui validate`

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
uvicorn backend.main:app --port 8001
# or
python -m backend.main
```

## Architecture

```
backend/
  adapters/          SQLAlchemy adapter layer (PostgreSQL)
  pattern_cache/     Fuzzy trigger matching and pattern caching
  skill_registry/    Skill/enum YAML loading, validation, LLM formatting
    cli/             Click CLI entry points
    config/          Pydantic Settings (DYNAMO_PG_*, DYNAMO_SKILL_*, DYNAMO_CACHE_*)
skills/              *.skill.yaml — entity definitions
enums/               *.enum.yaml — enum definitions
tests/               pytest test suite
```

## License

MIT — see [LICENSE](LICENSE).
