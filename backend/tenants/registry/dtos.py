"""DTOs for the tenant YAML registry REST layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ResourceType = Literal["skill", "enum", "pattern", "widget"]


class RegistryEntryRead(BaseModel):
    id: UUID
    tenant_id: UUID
    resource_type: ResourceType
    name: str
    yaml_source: str
    parsed_json: dict[str, Any]
    checksum: str
    created_at: datetime
    updated_at: datetime


class RegistryEntrySummary(BaseModel):
    """Lightweight projection used by list endpoints — omits the YAML source."""

    id: UUID
    name: str
    checksum: str
    updated_at: datetime


class RegistryUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    yaml_source: str = Field(min_length=1, max_length=200_000)
