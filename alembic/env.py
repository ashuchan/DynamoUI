"""
Alembic env.py for DynamoUI internal schema migrations.
Targets only the dynamoui_internal schema.
DB URL is read from InternalSettings (DYNAMO_INTERNAL_DB_URL or pg write URL fallback).
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from backend.metering.models.tables import configure_schema, metering_metadata
from backend.skill_registry.config.settings import internal_settings, pg_settings

# ---------------------------------------------------------------------------
# Alembic config object (alembic.ini)
# ---------------------------------------------------------------------------
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Bind schema and metadata
# ---------------------------------------------------------------------------
configure_schema(internal_settings.db_schema)
target_metadata = metering_metadata

DB_URL = internal_settings.resolved_db_url(pg_settings)


# ---------------------------------------------------------------------------
# Offline mode (generates SQL without a live DB connection)
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema=internal_settings.db_schema,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (runs against a live DB)
# ---------------------------------------------------------------------------
def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table="alembic_version",
        version_table_schema=internal_settings.db_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DB_URL)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
