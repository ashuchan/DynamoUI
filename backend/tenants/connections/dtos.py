"""Pydantic DTOs for the tenant connections REST layer.

Response models intentionally never include any plaintext credential — only
``has_password`` so the UI can render a "set password" placeholder.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    adapter_kind: str = Field(min_length=1, max_length=64)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65_535)
    database: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=4096)
    options: dict[str, Any] | None = None


class ConnectionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    adapter_kind: str
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    has_password: bool
    options: dict[str, Any]
    status: str
    last_tested_at: datetime | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    ok: bool
    status: str
    error: str | None = None
    tested_at: datetime
