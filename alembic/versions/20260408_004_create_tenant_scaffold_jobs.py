"""create_tenant_scaffold_jobs

Revision ID: 004_scaffold_jobs
Revises: 003_tenant_connections
Create Date: 2026-04-08

Phase 3 — async scaffold jobs against tenant DB connections.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_scaffold_jobs"
down_revision: Union[str, None] = "003_tenant_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    op.create_table(
        "tenant_scaffold_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("progress", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("result_summary", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{SCHEMA}.auth_tenants.id"],
            name="fk_tenant_scaffold_jobs_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            [f"{SCHEMA}.tenant_db_connections.id"],
            name="fk_tenant_scaffold_jobs_connection",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenant_scaffold_jobs_tenant_id",
        "tenant_scaffold_jobs",
        ["tenant_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenant_scaffold_jobs_tenant_id",
        table_name="tenant_scaffold_jobs",
        schema=SCHEMA,
    )
    op.drop_table("tenant_scaffold_jobs", schema=SCHEMA)
