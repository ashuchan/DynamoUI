"""create_v2_tables

Revision ID: 006_v2
Revises: 005_tenant_registry
Create Date: 2026-04-17

Creates all DynamoUI v2 tables in the existing dynamoui_internal schema:

Personalisation (M2):
  - dui_saved_view, dui_dashboard, dui_dashboard_tile, dui_pin

Scheduling (M5) + Alerts (M6):
  - dui_schedule, dui_alert, dui_delivery_run

Sharing (M8) + Verifier gap recording (M3):
  - dui_share_token, pattern_gap

See the corresponding ``tables.py`` modules for the canonical schema.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006_v2"
down_revision: Union[str, None] = "005_tenant_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "dynamoui_internal"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ------------------------------------------------------------------
    # Personalisation
    # ------------------------------------------------------------------
    op.create_table(
        "dui_saved_view",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("nl_input", sa.Text, nullable=False),
        sa.Column("query_plan_json", sa.JSON, nullable=False),
        sa.Column("entity", sa.String(255), nullable=False),
        sa.Column("result_shape", sa.String(32), nullable=False, server_default=sa.text("'list'")),
        sa.Column("is_shared", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("pattern_id_hint", sa.String(255), nullable=True),
        sa.Column("skill_hash", sa.String(64), nullable=False),
        sa.Column("stale", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_dui_saved_view_owner_name"),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_saved_view_owner", "dui_saved_view", ["owner_user_id"], schema=SCHEMA)

    op.create_table(
        "dui_dashboard",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("layout_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_dui_dashboard_owner_name"),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_dashboard_owner", "dui_dashboard", ["owner_user_id"], schema=SCHEMA)

    op.create_table(
        "dui_dashboard_tile",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("dashboard_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("position_x", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("position_y", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("width", sa.Integer, nullable=False, server_default=sa.text("4")),
        sa.Column("height", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("overrides_json", sa.JSON, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "source_type IN ('saved_view','widget','pattern_result','dashboard')",
            name="ck_dui_dashboard_tile_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id"],
            [f"{SCHEMA}.dui_dashboard.id"],
            name="fk_dui_dashboard_tile_dashboard",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_dashboard_tile_dashboard", "dui_dashboard_tile", ["dashboard_id"], schema=SCHEMA)

    op.create_table(
        "dui_pin",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "source_type", "source_id", name="uq_dui_pin_user_source"),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_pin_user", "dui_pin", ["user_id"], schema=SCHEMA)

    # ------------------------------------------------------------------
    # Scheduling + Alerts
    # ------------------------------------------------------------------
    op.create_table(
        "dui_schedule",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("cron_expr", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("channel_config_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("format", sa.String(32), nullable=False, server_default=sa.text("'csv'")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "source_type IN ('saved_view','dashboard')",
            name="ck_dui_schedule_source_type",
        ),
        sa.CheckConstraint(
            "channel IN ('email','slack','webhook')",
            name="ck_dui_schedule_channel",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_schedule_owner", "dui_schedule", ["owner_user_id"], schema=SCHEMA)
    op.create_index("ix_dui_schedule_next_run", "dui_schedule", ["next_run_at"], schema=SCHEMA)

    op.create_table(
        "dui_alert",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("saved_view_id", sa.UUID(), nullable=False),
        sa.Column("condition_json", sa.JSON, nullable=False),
        sa.Column("check_cron", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("channel_config_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("last_triggered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_alert_owner", "dui_alert", ["owner_user_id"], schema=SCHEMA)

    op.create_table(
        "dui_delivery_run",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("schedule_id", sa.UUID(), nullable=True),
        sa.Column("alert_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'running'")),
        sa.Column("rows_delivered", sa.Integer, nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_dui_delivery_run_schedule", "dui_delivery_run", ["schedule_id"], schema=SCHEMA)
    op.create_index("ix_dui_delivery_run_alert", "dui_delivery_run", ["alert_id"], schema=SCHEMA)

    # ------------------------------------------------------------------
    # Sharing + Pattern gaps
    # ------------------------------------------------------------------
    op.create_table(
        "dui_share_token",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("max_access_count", sa.Integer, nullable=True),
        sa.Column("access_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "source_type IN ('saved_view','dashboard','widget','pattern_result')",
            name="ck_dui_share_token_source_type",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_dui_share_token_source",
        "dui_share_token",
        ["source_type", "source_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "pattern_gap",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("user_input", sa.Text, nullable=False),
        sa.Column("rejected_candidate_json", sa.JSON, nullable=False),
        sa.Column("llm_plan_json", sa.JSON, nullable=True),
        sa.Column("gap_suggestion_json", sa.JSON, nullable=True),
        sa.Column("entity", sa.String(255), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("resolution_type", sa.String(32), nullable=True),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("input_hash", name="uq_pattern_gap_input_hash"),
        schema=SCHEMA,
    )
    op.create_index("ix_pattern_gap_entity", "pattern_gap", ["entity"], schema=SCHEMA)
    op.create_index("ix_pattern_gap_resolved", "pattern_gap", ["resolved"], schema=SCHEMA)


def downgrade() -> None:
    for name in (
        "pattern_gap",
        "dui_share_token",
        "dui_delivery_run",
        "dui_alert",
        "dui_schedule",
        "dui_pin",
        "dui_dashboard_tile",
        "dui_dashboard",
        "dui_saved_view",
    ):
        op.drop_table(name, schema=SCHEMA)
