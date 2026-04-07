"""create_tenant_registry

Revision ID: 005_tenant_registry
Revises: 004_scaffold_jobs
Create Date: 2026-04-08

Phase 4 — tenant-scoped YAML registry tables (skills, enums, patterns, widgets).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_tenant_registry"
down_revision: Union[str, None] = "004_scaffold_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"
TABLES = ("tenant_skills", "tenant_enums", "tenant_patterns", "tenant_widgets")


def upgrade() -> None:
    for name in TABLES:
        op.create_table(
            name,
            sa.Column("id", sa.UUID(), primary_key=True),
            sa.Column("tenant_id", sa.UUID(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("yaml_source", sa.Text, nullable=False),
            sa.Column("parsed_json", sa.JSON, nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
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
            sa.UniqueConstraint("tenant_id", "name", name=f"uq_{name}_tenant_name"),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                [f"{SCHEMA}.auth_tenants.id"],
                name=f"fk_{name}_tenant",
                ondelete="CASCADE",
            ),
            schema=SCHEMA,
        )
        op.create_index(
            f"ix_{name}_tenant_id",
            name,
            ["tenant_id"],
            schema=SCHEMA,
        )


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_index(f"ix_{name}_tenant_id", table_name=name, schema=SCHEMA)
        op.drop_table(name, schema=SCHEMA)
