"""DTOs for metering_cost_rates rows."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class CostRateCreateDTO(BaseModel):
    """
    Fields required to insert a new cost rate entry.
    Both change_reason and created_by are mandatory — the DAO will
    raise ValueError if either is missing or blank.
    """

    provider: str
    model: str
    input_cost_per_1k: Decimal
    output_cost_per_1k: Decimal
    thinking_cost_per_1k: Decimal | None = None
    effective_from: date
    source_reference: str | None = None
    change_reason: str = Field(..., min_length=5)
    created_by: str = Field(..., min_length=1)

    @field_validator("change_reason", "created_by")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CostRateReadDTO(BaseModel):
    """Full row shape returned from DAO reads."""

    id: int
    provider: str
    model: str
    input_cost_per_1k: Decimal
    output_cost_per_1k: Decimal
    thinking_cost_per_1k: Decimal | None
    effective_from: date
    effective_to: date | None
    change_reason: str
    source_reference: str | None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}
