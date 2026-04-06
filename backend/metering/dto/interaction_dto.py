"""DTOs for metering_llm_interactions rows."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class LLMInteractionCreateDTO(BaseModel):
    """
    Fields captured by MeteringLLMProvider after each LLM call.
    cost_usd and cost_rate_id are NOT included — the service resolves
    them via CostCalculator before the DAO insert.
    """

    id: UUID
    operation_id: UUID
    tenant_id: UUID | None = None
    interaction_type: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    thinking_tokens: int = 0
    total_tokens: int
    thinking_summary: str | None = None
    latency_ms: int
    success: bool
    error_message: str | None = None


class LLMInteractionReadDTO(BaseModel):
    """Full row shape returned from DAO reads."""

    id: UUID
    operation_id: UUID
    tenant_id: UUID | None
    interaction_type: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    thinking_tokens: int
    total_tokens: int
    thinking_summary: str | None
    cost_usd: Decimal
    cost_rate_id: int | None
    latency_ms: int
    success: bool
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
