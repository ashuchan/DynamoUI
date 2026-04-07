"""DTOs for the scaffold jobs API."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ScaffoldJobRead(BaseModel):
    id: UUID
    tenant_id: UUID
    connection_id: UUID
    status: str
    progress: int
    result_summary: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class ScaffoldStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_filter: str | None = None
    table_filter: list[str] | None = None
