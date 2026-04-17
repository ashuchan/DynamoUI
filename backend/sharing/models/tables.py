"""Share tokens — opaque tokens hashed with bcrypt in the DB."""
from __future__ import annotations

import sqlalchemy as sa

sharing_metadata = sa.MetaData()


share_tokens = sa.Table(
    "dui_share_token",
    sharing_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("source_type", sa.String(32), nullable=False),
    sa.Column("source_id", sa.String(255), nullable=False),
    sa.Column("token_hash", sa.String(128), nullable=False),
    sa.Column("created_by_user_id", sa.UUID, nullable=False),
    sa.Column("tenant_id", sa.UUID, nullable=False),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("max_access_count", sa.Integer, nullable=True),
    sa.Column("access_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.CheckConstraint(
        "source_type IN ('saved_view','dashboard','widget','pattern_result')",
        name="ck_dui_share_token_source_type",
    ),
)


# Pattern-gap table used by the verifier's gap recorder.
pattern_gaps = sa.Table(
    "pattern_gap",
    sharing_metadata,
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("input_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("user_input", sa.Text, nullable=False),
    sa.Column("rejected_candidate_json", sa.JSON, nullable=False),
    sa.Column("llm_plan_json", sa.JSON, nullable=True),
    sa.Column("gap_suggestion_json", sa.JSON, nullable=True),
    sa.Column("entity", sa.String(255), nullable=True),
    sa.Column("user_id", sa.UUID, nullable=True),
    sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    sa.Column("resolution_type", sa.String(32), nullable=True),
    sa.Column("reviewed_by_user_id", sa.UUID, nullable=True),
    sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True),
        nullable=False, server_default=sa.text("NOW()"),
    ),
)


sa.Index("ix_dui_share_token_source", share_tokens.c.source_type, share_tokens.c.source_id)
sa.Index("ix_pattern_gap_entity", pattern_gaps.c.entity)
sa.Index("ix_pattern_gap_resolved", pattern_gaps.c.resolved)


def configure_schema(schema_name: str) -> None:
    for table in sharing_metadata.tables.values():
        table.schema = schema_name
    sharing_metadata.schema = schema_name
