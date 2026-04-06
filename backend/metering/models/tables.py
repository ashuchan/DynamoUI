"""
SQLAlchemy Core table definitions for the DynamoUI metering subsystem.
All tables live in the `dynamoui_internal` schema (configurable via DYNAMO_INTERNAL_SCHEMA).

These definitions are the canonical source of truth for the internal schema.
The human-readable DDL at backend/metering/schema/metering_schema.sql is derived
from these via `python -m backend.metering.schema.export`.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import MetaData

# Separate MetaData so Alembic can target only the internal schema.
# schema is injected at runtime via _configure_schema().
metering_metadata = MetaData()

# ---------------------------------------------------------------------------
# Tables — defined with schema=None initially; configure_schema() sets it.
# ---------------------------------------------------------------------------

metering_cost_rates = sa.Table(
    "metering_cost_rates",
    metering_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("provider", sa.String(64), nullable=False),
    sa.Column("model", sa.String(128), nullable=False),
    sa.Column("input_cost_per_1k", sa.Numeric(10, 8), nullable=False),
    sa.Column("output_cost_per_1k", sa.Numeric(10, 8), nullable=False),
    sa.Column("thinking_cost_per_1k", sa.Numeric(10, 8), nullable=True),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("effective_to", sa.Date, nullable=True),
    sa.Column("change_reason", sa.Text, nullable=False),
    sa.Column("source_reference", sa.Text, nullable=True),
    sa.Column("created_by", sa.String(255), nullable=False),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
    sa.UniqueConstraint("provider", "model", "effective_from", name="uq_cost_rates_provider_model_date"),
    sa.CheckConstraint(
        "effective_to IS NULL OR effective_to >= effective_from",
        name="ck_cost_rates_date_range",
    ),
)

metering_operations = sa.Table(
    "metering_operations",
    metering_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("tenant_id", sa.UUID, nullable=True),
    sa.Column("session_id", sa.UUID, nullable=True),
    sa.Column("user_id", sa.String(255), nullable=True),
    sa.Column("operation_type", sa.String(64), nullable=False),
    sa.Column("user_input_hash", sa.String(64), nullable=True),
    sa.Column("entity", sa.String(255), nullable=True),
    sa.Column("intent", sa.String(255), nullable=True),
    sa.Column("pattern_id", sa.String(255), nullable=True),
    sa.Column("cache_hit", sa.Boolean, nullable=True),
    sa.Column("confidence", sa.Float, nullable=True),
    sa.Column("success", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("rows_returned", sa.Integer, nullable=True),
    sa.Column("duration_ms", sa.Integer, nullable=True),
    sa.Column("ip_address", sa.String(45), nullable=True),
    sa.Column("metadata", sa.JSON, nullable=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
)

metering_llm_interactions = sa.Table(
    "metering_llm_interactions",
    metering_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("operation_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=True),
    sa.Column("interaction_type", sa.String(64), nullable=False),
    sa.Column("provider", sa.String(64), nullable=False),
    sa.Column("model", sa.String(128), nullable=False),
    sa.Column("prompt_tokens", sa.Integer, nullable=False),
    sa.Column("completion_tokens", sa.Integer, nullable=False),
    sa.Column("thinking_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("total_tokens", sa.Integer, nullable=False),
    sa.Column("thinking_summary", sa.Text, nullable=True),
    sa.Column("cost_usd", sa.Numeric(14, 8), nullable=False),
    sa.Column("cost_rate_id", sa.Integer, nullable=True),
    sa.Column("latency_ms", sa.Integer, nullable=False),
    sa.Column("success", sa.Boolean, nullable=False),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
)

# ---------------------------------------------------------------------------
# Indexes (created separately so Alembic can diff them)
# ---------------------------------------------------------------------------

sa.Index("ix_metering_operations_tenant_id", metering_operations.c.tenant_id)
sa.Index("ix_metering_operations_created_at", metering_operations.c.created_at)
sa.Index("ix_metering_operations_operation_type", metering_operations.c.operation_type)
sa.Index("ix_metering_operations_entity", metering_operations.c.entity)

sa.Index("ix_metering_llm_interactions_operation_id", metering_llm_interactions.c.operation_id)
sa.Index("ix_metering_llm_interactions_tenant_id", metering_llm_interactions.c.tenant_id)
sa.Index("ix_metering_llm_interactions_created_at", metering_llm_interactions.c.created_at)
sa.Index(
    "ix_metering_llm_interactions_provider_model",
    metering_llm_interactions.c.provider,
    metering_llm_interactions.c.model,
)
sa.Index("ix_metering_llm_interactions_cost_rate_id", metering_llm_interactions.c.cost_rate_id)


def configure_schema(schema_name: str) -> None:
    """
    Bind all metering tables to the given PostgreSQL schema.
    Must be called once at startup before any table is used.
    Idempotent — safe to call multiple times with the same value.
    """
    for table in metering_metadata.tables.values():
        table.schema = schema_name
    metering_metadata.schema = schema_name
