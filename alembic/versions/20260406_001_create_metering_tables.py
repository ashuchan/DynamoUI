"""create_metering_tables

Revision ID: 001_metering
Revises:
Create Date: 2026-04-06

Creates the dynamoui_internal schema and all metering tables:
  - metering_cost_rates   (append-only pricing ledger)
  - metering_operations   (one row per user-visible LLM-invoking operation)
  - metering_llm_interactions  (one row per actual LLM API call)

Also seeds initial cost rates for the default providers/models.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001_metering"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    # ── Schema ─────────────────────────────────────────────────────────────
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # Grant write user access to the schema only if the role exists (idempotent)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dynamoui_writer') THEN
                GRANT USAGE ON SCHEMA {SCHEMA} TO dynamoui_writer;
                ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA}
                    GRANT SELECT, INSERT, UPDATE ON TABLES TO dynamoui_writer;
            END IF;
        END
        $$;
        """
    )

    # ── metering_cost_rates ─────────────────────────────────────────────────
    op.create_table(
        "metering_cost_rates",
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
        sa.UniqueConstraint(
            "provider", "model", "effective_from",
            name="uq_cost_rates_provider_model_date"
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_cost_rates_date_range",
        ),
        schema=SCHEMA,
    )

    # ── metering_operations ─────────────────────────────────────────────────
    op.create_table(
        "metering_operations",
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
        sa.Column(
            "success", sa.Boolean, nullable=False, server_default=sa.text("TRUE")
        ),
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
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metering_operations_tenant_id",
        "metering_operations", ["tenant_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_operations_created_at",
        "metering_operations", ["created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_operations_operation_type",
        "metering_operations", ["operation_type"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_operations_entity",
        "metering_operations", ["entity"], schema=SCHEMA
    )

    # ── metering_llm_interactions ───────────────────────────────────────────
    op.create_table(
        "metering_llm_interactions",
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column(
            "operation_id", sa.UUID, nullable=False,
            # FK enforced at app layer; omitted here to avoid cross-schema FK complexity
        ),
        sa.Column("tenant_id", sa.UUID, nullable=True),
        sa.Column("interaction_type", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=False),
        sa.Column("completion_tokens", sa.Integer, nullable=False),
        sa.Column(
            "thinking_tokens", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
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
        schema=SCHEMA,
    )
    op.create_index(
        "ix_metering_llm_interactions_operation_id",
        "metering_llm_interactions", ["operation_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_llm_interactions_tenant_id",
        "metering_llm_interactions", ["tenant_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_llm_interactions_created_at",
        "metering_llm_interactions", ["created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_llm_interactions_provider_model",
        "metering_llm_interactions", ["provider", "model"], schema=SCHEMA
    )
    op.create_index(
        "ix_metering_llm_interactions_cost_rate_id",
        "metering_llm_interactions", ["cost_rate_id"], schema=SCHEMA
    )

    # ── Seed initial cost rates ─────────────────────────────────────────────
    # Prices accurate as of April 2026 — verify against official pricing before
    # deploying to production and insert a new row via POST /metering/cost-rates
    # if rates have changed.
    seed_date = date(2025, 1, 1)
    op.bulk_insert(
        sa.table(
            "metering_cost_rates",
            sa.column("provider", sa.String),
            sa.column("model", sa.String),
            sa.column("input_cost_per_1k", sa.Numeric),
            sa.column("output_cost_per_1k", sa.Numeric),
            sa.column("thinking_cost_per_1k", sa.Numeric),
            sa.column("effective_from", sa.Date),
            sa.column("effective_to", sa.Date),
            sa.column("change_reason", sa.Text),
            sa.column("source_reference", sa.Text),
            sa.column("created_by", sa.String),
            schema=SCHEMA,
        ),
        [
            {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "input_cost_per_1k": "0.00080000",
                "output_cost_per_1k": "0.00400000",
                "thinking_cost_per_1k": "0.00080000",
                "effective_from": seed_date,
                "effective_to": None,
                "change_reason": "Initial seed — Anthropic Claude Haiku 4.5 pricing",
                "source_reference": "https://www.anthropic.com/pricing",
                "created_by": "seed",
            },
            {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "input_cost_per_1k": "0.00300000",
                "output_cost_per_1k": "0.01500000",
                "thinking_cost_per_1k": "0.00300000",
                "effective_from": seed_date,
                "effective_to": None,
                "change_reason": "Initial seed — Anthropic Claude Sonnet 4.6 pricing",
                "source_reference": "https://www.anthropic.com/pricing",
                "created_by": "seed",
            },
            {
                "provider": "google",
                "model": "gemini-1.5-flash",
                "input_cost_per_1k": "0.00007000",
                "output_cost_per_1k": "0.00030000",
                "thinking_cost_per_1k": None,
                "effective_from": seed_date,
                "effective_to": None,
                "change_reason": "Initial seed — Google Gemini 1.5 Flash pricing",
                "source_reference": "https://cloud.google.com/vertex-ai/generative-ai/pricing",
                "created_by": "seed",
            },
        ],
    )


def downgrade() -> None:
    SCHEMA = "dynamoui_internal"
    op.drop_table("metering_llm_interactions", schema=SCHEMA)
    op.drop_table("metering_operations", schema=SCHEMA)
    op.drop_table("metering_cost_rates", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
