"""create_tenant_connections

Revision ID: 003_tenant_connections
Revises: 002_auth
Create Date: 2026-04-08

Creates ``tenant_db_connections`` for Phase 2 — the encrypted, tenant-scoped
DB connection registry. See ``docs/MULTI_TENANT_PLAN.md``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_tenant_connections"
down_revision: Union[str, None] = "002_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    op.create_table(
        "tenant_db_connections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("adapter_kind", sa.String(64), nullable=False),
        sa.Column("host", sa.String(255), nullable=True),
        sa.Column("port", sa.Integer, nullable=True),
        sa.Column("database", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("encrypted_secret", sa.Text, nullable=True),
        sa.Column(
            "options_json",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'untested'"),
        ),
        sa.Column("last_tested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_test_error", sa.Text, nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "name", name="uq_tenant_db_connections_tenant_name"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{SCHEMA}.auth_tenants.id"],
            name="fk_tenant_db_connections_tenant",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenant_db_connections_tenant_id",
        "tenant_db_connections",
        ["tenant_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tenant_db_connections_tenant_id",
        table_name="tenant_db_connections",
        schema=SCHEMA,
    )
    op.drop_table("tenant_db_connections", schema=SCHEMA)
