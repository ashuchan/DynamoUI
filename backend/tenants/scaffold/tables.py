"""SQLAlchemy Core table for ``tenant_scaffold_jobs``."""
from __future__ import annotations

import sqlalchemy as sa

scaffold_metadata = sa.MetaData()

tenant_scaffold_jobs = sa.Table(
    "tenant_scaffold_jobs",
    scaffold_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("connection_id", sa.UUID, nullable=False),
    sa.Column(
        "status",
        sa.String(32),
        nullable=False,
        server_default=sa.text("'pending'"),
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
)

sa.Index(
    "ix_tenant_scaffold_jobs_tenant_id",
    tenant_scaffold_jobs.c.tenant_id,
)


def configure_schema(schema_name: str) -> None:
    for table in scaffold_metadata.tables.values():
        table.schema = schema_name
    scaffold_metadata.schema = schema_name
