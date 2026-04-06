"""DTOs for metering_operations rows."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class OperationCreateDTO(BaseModel):
    """Fields required to open a new metering operation row."""

    id: UUID
    operation_type: str
    tenant_id: UUID | None = None
    session_id: UUID | None = None
    user_id: str | None = None
    user_input_hash: str | None = None
    ip_address: str | None = None
    metadata: dict | None = None


class OperationUpdateDTO(BaseModel):
    """
    Fields written back once the operation completes.
    All are optional — only non-None fields are applied.
    """

    entity: str | None = None
    intent: str | None = None
    pattern_id: str | None = None
    cache_hit: bool | None = None
    confidence: float | None = None
    success: bool = True
    error_message: str | None = None
    rows_returned: int | None = None
    duration_ms: int | None = None


class OperationReadDTO(BaseModel):
    """Full row shape returned from DAO reads."""

    id: UUID
    operation_type: str
    tenant_id: UUID | None
    session_id: UUID | None
    user_id: str | None
    user_input_hash: str | None
    entity: str | None
    intent: str | None
    pattern_id: str | None
    cache_hit: bool | None
    confidence: float | None
    success: bool
    error_message: str | None
    rows_returned: int | None
    duration_ms: int | None
    ip_address: str | None
    metadata: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}
