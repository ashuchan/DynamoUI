"""Tenant-scoped registry tables.

Each row stores the canonical YAML source plus its parsed JSON projection.
The checksum + updated_at columns let the runtime cache decide whether a
cached view is still fresh without re-parsing the YAML on every request.
"""
from __future__ import annotations

import sqlalchemy as sa

registry_metadata = sa.MetaData()


def _resource_table(name: str) -> sa.Table:
    return sa.Table(
        name,
        registry_metadata,
        sa.Column("id", sa.UUID, primary_key=True),
        sa.Column("tenant_id", sa.UUID, nullable=False),
        # Logical name within the tenant — must be unique per tenant.
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
    )


tenant_skills = _resource_table("tenant_skills")
tenant_enums = _resource_table("tenant_enums")
tenant_patterns = _resource_table("tenant_patterns")
tenant_widgets = _resource_table("tenant_widgets")

# Tenant-scoped indexes so list endpoints stay fast even at thousands of rows.
for _table in (tenant_skills, tenant_enums, tenant_patterns, tenant_widgets):
    sa.Index(f"ix_{_table.name}_tenant_id", _table.c.tenant_id)


RESOURCE_TABLES = {
    "skill": tenant_skills,
    "enum": tenant_enums,
    "pattern": tenant_patterns,
    "widget": tenant_widgets,
}


def configure_schema(schema_name: str) -> None:
    for table in registry_metadata.tables.values():
        table.schema = schema_name
    registry_metadata.schema = schema_name
