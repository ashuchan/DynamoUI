"""create_auth_tables

Revision ID: 002_auth
Revises: 001_metering
Create Date: 2026-04-07

Creates the auth subsystem tables in the dynamoui_internal schema:
  - auth_tenants
  - auth_users
  - auth_tenant_users
  - auth_oauth_identities

See ``backend/auth/models/tables.py`` for the canonical table definitions.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_auth"
down_revision: Union[str, None] = "001_metering"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    # Schema already created by the metering migration. Ensure for safety.
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "auth_tenants",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'active'")
        ),
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
        sa.UniqueConstraint("slug", name="uq_auth_tenants_slug"),
        schema=SCHEMA,
    )

    op.create_table(
        "auth_users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(512), nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_auth_users_email"),
        schema=SCHEMA,
    )

    op.create_table(
        "auth_tenant_users",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role", sa.String(32), nullable=False, server_default=sa.text("'member'")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id", name="pk_auth_tenant_users"),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')",
            name="ck_auth_tenant_users_role",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{SCHEMA}.auth_tenants.id"],
            name="fk_auth_tenant_users_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.auth_users.id"],
            name="fk_auth_tenant_users_user",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_auth_tenant_users_user_id",
        "auth_tenant_users",
        ["user_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "auth_oauth_identities",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "provider", "subject", name="uq_auth_oauth_identities_provider_subject"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.auth_users.id"],
            name="fk_auth_oauth_identities_user",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_auth_oauth_identities_user_id",
        "auth_oauth_identities",
        ["user_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_oauth_identities_user_id",
        table_name="auth_oauth_identities",
        schema=SCHEMA,
    )
    op.drop_table("auth_oauth_identities", schema=SCHEMA)
    op.drop_index(
        "ix_auth_tenant_users_user_id",
        table_name="auth_tenant_users",
        schema=SCHEMA,
    )
    op.drop_table("auth_tenant_users", schema=SCHEMA)
    op.drop_table("auth_users", schema=SCHEMA)
    op.drop_table("auth_tenants", schema=SCHEMA)
