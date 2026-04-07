"""SQLAlchemy Core tables for the tenant-scoped connection registry.

Lives in the ``dynamoui_internal`` schema. The ``encrypted_secret`` column
stores an envelope produced by :mod:`backend.crypto.envelope` — never raw
credentials.
"""
from __future__ import annotations

import sqlalchemy as sa

connections_metadata = sa.MetaData()

tenant_db_connections = sa.Table(
    "tenant_db_connections",
    connections_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("name", sa.String(128), nullable=False),
    sa.Column("adapter_kind", sa.String(64), nullable=False),
    sa.Column("host", sa.String(255), nullable=True),
    sa.Column("port", sa.Integer, nullable=True),
    sa.Column("database", sa.String(255), nullable=True),
    sa.Column("username", sa.String(255), nullable=True),
    # Envelope JSON produced by backend.crypto.envelope.encrypt — TEXT, not VARCHAR.
    sa.Column("encrypted_secret", sa.Text, nullable=True),
    # Adapter-specific options (e.g. AWS region, GCP project id, ssl options).
    sa.Column("options_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
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
    sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_db_connections_tenant_name"),
)

sa.Index(
    "ix_tenant_db_connections_tenant_id",
    tenant_db_connections.c.tenant_id,
)


def configure_schema(schema_name: str) -> None:
    """Bind every connections table to the given schema. Idempotent."""
    for table in connections_metadata.tables.values():
        table.schema = schema_name
    connections_metadata.schema = schema_name
