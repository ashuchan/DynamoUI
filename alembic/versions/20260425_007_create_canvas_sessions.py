"""create_canvas_sessions

Revision ID: 007_canvas
Revises: 006_v2
Create Date: 2026-04-25

Creates the canvas_sessions table for the Canvas conversational generator.
Lives in the existing dynamoui_internal schema.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "007_canvas"
down_revision: Union[str, None] = "006_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "canvas_sessions",
        sa.Column("session_id", sa.UUID(), primary_key=True),
        sa.Column("operator_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'eliciting'")),
        sa.Column("messages", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("partial_intent", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("preview_cache", JSONB, nullable=True),
        sa.Column("preview_cache_key", sa.Text, nullable=True),
        sa.Column("skill_yaml_context", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_canvas_sessions_operator", "canvas_sessions", ["operator_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_canvas_sessions_tenant", "canvas_sessions", ["tenant_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_canvas_sessions_updated_at",
        "canvas_sessions",
        ["updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_canvas_sessions_updated_at", table_name="canvas_sessions", schema=SCHEMA)
    op.drop_index("ix_canvas_sessions_tenant", table_name="canvas_sessions", schema=SCHEMA)
    op.drop_index("ix_canvas_sessions_operator", table_name="canvas_sessions", schema=SCHEMA)
    op.drop_table("canvas_sessions", schema=SCHEMA)
