"""SQLAlchemy Core table definitions for the auth subsystem.

All tables live in the ``dynamoui_internal`` schema (same as the metering
tables). The ``configure_schema`` helper binds the tables to the runtime-
configured schema, mirroring the metering module.
"""
from __future__ import annotations

import sqlalchemy as sa

auth_metadata = sa.MetaData()

# ---------------------------------------------------------------------------
# tenants
# ---------------------------------------------------------------------------
tenants = sa.Table(
    "auth_tenants",
    auth_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("slug", sa.String(64), nullable=False, unique=True),
    sa.Column(
        "status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'active'"),
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
)

# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
users = sa.Table(
    "auth_users",
    auth_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("email", sa.String(320), nullable=False, unique=True),
    sa.Column("display_name", sa.String(255), nullable=True),
    # password_hash is NULL for users that only log in via OAuth.
    sa.Column("password_hash", sa.String(512), nullable=True),
    sa.Column(
        "status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'active'"),
    ),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
    sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# ---------------------------------------------------------------------------
# tenant_users — N:M with role; unique on (tenant_id, user_id)
# ---------------------------------------------------------------------------
tenant_users = sa.Table(
    "auth_tenant_users",
    auth_metadata,
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("user_id", sa.UUID, nullable=False),
    sa.Column(
        "role",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'member'"),
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
)

# ---------------------------------------------------------------------------
# oauth_identities — external identity providers (Google only in Phase 1)
# ---------------------------------------------------------------------------
oauth_identities = sa.Table(
    "auth_oauth_identities",
    auth_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("user_id", sa.UUID, nullable=False),
    sa.Column("provider", sa.String(32), nullable=False),
    # ``subject`` is the provider-stable user id (Google "sub" claim).
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
)

# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------
sa.Index("ix_auth_tenant_users_user_id", tenant_users.c.user_id)
sa.Index("ix_auth_oauth_identities_user_id", oauth_identities.c.user_id)


def configure_schema(schema_name: str) -> None:
    """Bind every auth table to the given PostgreSQL schema. Idempotent."""
    for table in auth_metadata.tables.values():
        table.schema = schema_name
    auth_metadata.schema = schema_name
