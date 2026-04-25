"""Wire-contract schemas — must mirror dynamoui-frontend/src/types/canvas.ts.

Any change here MUST be matched in the frontend types in the same PR.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.canvas.models.intent import (
    AestheticMood,
    Domain,
    DensityPreference,
    LayoutArchetype,
    OperationProfile,
)
from backend.canvas.models.session import CanvasMessage, ConversationState


# ---------------------------------------------------------------------------
# POST /session
# ---------------------------------------------------------------------------
class CreateSessionRequest(BaseModel):
    skill_yaml_context: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# GET /session/{id}
# ---------------------------------------------------------------------------
class SessionView(BaseModel):
    """Full session — used by the FE for rehydration on reload."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    state: ConversationState
    messages: list[CanvasMessage]
    partial_intent: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# POST /session/{id}/message
# ---------------------------------------------------------------------------
class SendMessageRequest(BaseModel):
    message: str


class MessageResponse(BaseModel):
    """Mirrors MessageResponse in canvas.ts."""

    reply: str
    intent_update: dict[str, Any] | None = None
    session_state: ConversationState


# ---------------------------------------------------------------------------
# GET /session/{id}/intent
# ---------------------------------------------------------------------------
class IntentEnvelope(BaseModel):
    """Wrapped form (per LLD §4 fix). FE polls this every 2s."""

    intent: dict[str, Any] = Field(default_factory=dict)
    state: ConversationState


# ---------------------------------------------------------------------------
# GET /session/{id}/preview
# ---------------------------------------------------------------------------
class PreviewField(BaseModel):
    name: str
    label: str
    display_hint: str
    column_priority: Literal["high", "medium", "low"]
    is_status: bool = False
    is_monetary: bool = False


class PreviewData(BaseModel):
    """Live preview — theme CSS inlined for ScopedThemeProvider on the FE."""

    entity: str
    fields: list[PreviewField]
    rows: list[dict[str, Any]]
    archetype: LayoutArchetype
    theme_css: str
    nav_style: Literal["sidebar", "top_nav"]
    metric_fields: list[str]


# ---------------------------------------------------------------------------
# POST /session/{id}/generate
# ---------------------------------------------------------------------------
class GenerateResponse(BaseModel):
    status: Literal["ok"]
    files: list[str]
    artifacts_url: str


# ---------------------------------------------------------------------------
# Re-exports so the router can stay terse
# ---------------------------------------------------------------------------
__all__ = [
    "AestheticMood",
    "ConversationState",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "DensityPreference",
    "Domain",
    "GenerateResponse",
    "IntentEnvelope",
    "LayoutArchetype",
    "MessageResponse",
    "OperationProfile",
    "PreviewData",
    "PreviewField",
    "SendMessageRequest",
    "SessionView",
]
