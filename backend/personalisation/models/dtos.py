"""Pydantic DTOs for the personalisation router — the wire contract."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SavedViewCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    nlInput: str
    queryPlan: dict
    entity: str
    resultShape: Literal["list", "single", "aggregate", "chart"] = "list"
    patternIdHint: str | None = None
    isShared: bool = False


class SavedViewUpdate(BaseModel):
    name: str | None = None
    isShared: bool | None = None


class SavedViewRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    ownerUserId: UUID
    name: str
    nlInput: str
    queryPlan: dict
    entity: str
    resultShape: str
    isShared: bool
    patternIdHint: str | None
    skillHash: str
    stale: bool
    createdAt: datetime
    updatedAt: datetime


class DashboardLayout(BaseModel):
    grid: Literal["12col"] = "12col"
    tiles: list[dict] = Field(default_factory=list)


class DashboardCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    layout: DashboardLayout = Field(default_factory=DashboardLayout)


class DashboardUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    layout: DashboardLayout | None = None


class DashboardRead(BaseModel):
    id: UUID
    ownerUserId: UUID
    name: str
    description: str | None
    isDefault: bool
    layout: DashboardLayout
    createdAt: datetime
    updatedAt: datetime


class TileCreate(BaseModel):
    sourceType: Literal["saved_view", "widget", "pattern_result"]
    sourceId: str
    positionX: int = 0
    positionY: int = 0
    width: int = 4
    height: int = 3
    overrides: dict | None = None


class TileUpdate(BaseModel):
    positionX: int | None = None
    positionY: int | None = None
    width: int | None = None
    height: int | None = None
    overrides: dict | None = None


class TileRead(BaseModel):
    id: UUID
    dashboardId: UUID
    sourceType: str
    sourceId: str
    position: dict
    overrides: dict | None


class PinCreate(BaseModel):
    sourceType: Literal["saved_view", "widget", "pattern_result", "dashboard"]
    sourceId: str


class PinRead(BaseModel):
    id: UUID
    userId: UUID
    sourceType: str
    sourceId: str
    position: int
    createdAt: datetime
