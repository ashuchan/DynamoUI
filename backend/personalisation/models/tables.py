"""SQLAlchemy Core tables for the personalisation subsystem.

Lives in the `dynamoui_internal` schema (reuses the existing env var).
Note: the v2 plan calls this schema `dynamoui`; we collapse it into the
existing `dynamoui_internal` schema to match the auth/metering/connections
tables already deployed. See ``MISALIGNMENTS.md`` for the rationale.
"""
from __future__ import annotations

import sqlalchemy as sa

personalisation_metadata = sa.MetaData()


saved_views = sa.Table(
    "dui_saved_view",
    personalisation_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("owner_user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("nl_input", sa.Text, nullable=False),
    sa.Column("query_plan_json", sa.JSON, nullable=False),
    sa.Column("entity", sa.String(255), nullable=False),
    sa.Column("result_shape", sa.String(32), nullable=False, server_default=sa.text("'list'")),
    sa.Column("is_shared", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    sa.Column("pattern_id_hint", sa.String(255), nullable=True),
    sa.Column("skill_hash", sa.String(64), nullable=False),
    sa.Column("stale", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.UniqueConstraint("owner_user_id", "name", name="uq_dui_saved_view_owner_name"),
)


dashboards = sa.Table(
    "dui_dashboard",
    personalisation_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("owner_user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("name", sa.String(255), nullable=False),
    sa.Column("description", sa.Text, nullable=True),
    sa.Column("layout_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.UniqueConstraint("owner_user_id", "name", name="uq_dui_dashboard_owner_name"),
)


dashboard_tiles = sa.Table(
    "dui_dashboard_tile",
    personalisation_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("dashboard_id", sa.UUID, nullable=False),
    sa.Column("source_type", sa.String(32), nullable=False),
    sa.Column("source_id", sa.String(255), nullable=False),
    sa.Column("position_x", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("position_y", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("width", sa.Integer, nullable=False, server_default=sa.text("4")),
    sa.Column("height", sa.Integer, nullable=False, server_default=sa.text("3")),
    sa.Column("overrides_json", sa.JSON, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.CheckConstraint(
        "source_type IN ('saved_view','widget','pattern_result','dashboard')",
        name="ck_dui_dashboard_tile_source_type",
    ),
)


pins = sa.Table(
    "dui_pin",
    personalisation_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("source_type", sa.String(32), nullable=False),
    sa.Column("source_id", sa.String(255), nullable=False),
    sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.UniqueConstraint("user_id", "source_type", "source_id", name="uq_dui_pin_user_source"),
)


sa.Index("ix_dui_saved_view_owner", saved_views.c.owner_user_id)
sa.Index("ix_dui_dashboard_owner", dashboards.c.owner_user_id)
sa.Index("ix_dui_dashboard_tile_dashboard", dashboard_tiles.c.dashboard_id)
sa.Index("ix_dui_pin_user", pins.c.user_id)


def configure_schema(schema_name: str) -> None:
    for table in personalisation_metadata.tables.values():
        table.schema = schema_name
    personalisation_metadata.schema = schema_name
