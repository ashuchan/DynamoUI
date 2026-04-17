"""SQLAlchemy Core tables for scheduling + alerts.

Lives in ``dynamoui_internal`` alongside the rest of DynamoUI-owned state.
"""
from __future__ import annotations

import sqlalchemy as sa

scheduling_metadata = sa.MetaData()


schedules = sa.Table(
    "dui_schedule",
    scheduling_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("owner_user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
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
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.CheckConstraint(
        "source_type IN ('saved_view','dashboard')",
        name="ck_dui_schedule_source_type",
    ),
    sa.CheckConstraint(
        "channel IN ('email','slack','webhook')",
        name="ck_dui_schedule_channel",
    ),
)


alerts = sa.Table(
    "dui_alert",
    scheduling_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("owner_user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("saved_view_id", sa.UUID, nullable=False),
    sa.Column("condition_json", sa.JSON, nullable=False),
    sa.Column("check_cron", sa.String(64), nullable=False),
    sa.Column("channel", sa.String(32), nullable=False),
    sa.Column("channel_config_json", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
    sa.Column("last_triggered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("last_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
)


delivery_runs = sa.Table(
    "dui_delivery_run",
    scheduling_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("schedule_id", sa.UUID, nullable=True),
    sa.Column("alert_id", sa.UUID, nullable=True),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'running'")),
    sa.Column("rows_delivered", sa.Integer, nullable=True),
    sa.Column("error_text", sa.Text, nullable=True),
    sa.Column("latency_ms", sa.Integer, nullable=True),
)


sa.Index("ix_dui_schedule_owner", schedules.c.owner_user_id)
sa.Index("ix_dui_schedule_next_run", schedules.c.next_run_at)
sa.Index("ix_dui_alert_owner", alerts.c.owner_user_id)
sa.Index("ix_dui_delivery_run_schedule", delivery_runs.c.schedule_id)
sa.Index("ix_dui_delivery_run_alert", delivery_runs.c.alert_id)


def configure_schema(schema_name: str) -> None:
    for table in scheduling_metadata.tables.values():
        table.schema = schema_name
    scheduling_metadata.schema = schema_name
