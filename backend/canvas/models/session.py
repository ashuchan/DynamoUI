"""Canvas session model + SQLAlchemy table (LLD 9 §6.3)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID


class ConversationState(str, Enum):
    ELICITING = "eliciting"
    CONFIRMING = "confirming"
    COMPLETE = "complete"


class CanvasMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    timestamp: str  # ISO-8601


class CanvasSession(BaseModel):
    """Pydantic representation of a row from canvas_sessions."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    state: ConversationState = ConversationState.ELICITING
    messages: list[CanvasMessage] = Field(default_factory=list)
    partial_intent: dict[str, Any] = Field(default_factory=dict)
    skill_yaml_context: str = ""
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# SQLAlchemy table — schema configured at startup via configure_schema()
# (mirrors backend/auth/models/tables.py and other internal modules).
# ---------------------------------------------------------------------------
metadata = sa.MetaData()

canvas_sessions = sa.Table(
    "canvas_sessions",
    metadata,
    sa.Column("session_id", PG_UUID(as_uuid=True), primary_key=True),
    sa.Column("operator_id", PG_UUID(as_uuid=True), nullable=False, index=True),
    sa.Column("tenant_id", PG_UUID(as_uuid=True), nullable=False, index=True),
    sa.Column("state", sa.Text(), nullable=False, default=ConversationState.ELICITING.value),
    sa.Column("messages", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("partial_intent", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("preview_cache", JSONB, nullable=True),  # cached PreviewData keyed by intent hash
    sa.Column("preview_cache_key", sa.Text(), nullable=True),
    sa.Column("skill_yaml_context", sa.Text(), nullable=False, server_default=""),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
)


def configure_schema(schema: str) -> None:
    """Bind the canvas_sessions table to the given Postgres schema.

    Called from main.py at startup (mirrors metering / auth / personalisation).
    """
    canvas_sessions.schema = schema
