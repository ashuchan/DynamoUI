"""Pydantic DTOs exchanged by the auth REST layer."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)
    tenant_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Optional explicit tenant name; defaults to display_name.",
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class GoogleLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str = Field(min_length=1)


class TenantSummary(BaseModel):
    id: UUID
    name: str
    slug: str
    role: str


class UserSummary(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserSummary
    tenant: TenantSummary
    tenants: list[TenantSummary]


class MeResponse(BaseModel):
    user: UserSummary
    tenant: TenantSummary
    tenants: list[TenantSummary]
