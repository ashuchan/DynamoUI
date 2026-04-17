"""Schedule + Alert DTOs. Field names follow the interaction contract (camelCase)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    sourceType: Literal["saved_view", "dashboard"]
    sourceId: str
    cronExpr: str
    timezone: str = "UTC"
    channel: Literal["email", "slack", "webhook"]
    channelConfig: dict = Field(default_factory=dict)
    format: Literal["csv", "xlsx", "html_snapshot", "pdf"] = "csv"


class ScheduleUpdate(BaseModel):
    cronExpr: str | None = None
    timezone: str | None = None
    channelConfig: dict | None = None
    format: str | None = None
    enabled: bool | None = None


class ScheduleRead(ScheduleCreate):
    id: UUID
    ownerUserId: UUID
    enabled: bool
    lastRunAt: datetime | None = None
    nextRunAt: datetime | None = None
    nextRuns: list[str] = Field(default_factory=list)
    failureCount: int
    createdAt: datetime
    updatedAt: datetime


class DeliveryRunRead(BaseModel):
    id: UUID
    scheduleId: UUID | None
    alertId: UUID | None
    startedAt: datetime
    finishedAt: datetime | None
    status: str
    rowsDelivered: int | None
    latencyMs: int | None
    errorText: str | None


class ScheduleDraft(BaseModel):
    sourceType: Literal["saved_view", "dashboard", "pattern", "synthesised"]
    cronExpr: str
    timezone: str
    channel: str
    channelConfig: dict
    format: str
    sourceSnapshot: dict
    nextRuns: list[str]


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class AlertCondition(BaseModel):
    type: Literal["row_count", "any_row_field", "aggregate"]
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: float | int | str
    field: str | None = None
    aggregate: Literal["sum", "avg", "min", "max"] | None = None


class AlertCreate(BaseModel):
    savedViewId: UUID
    condition: AlertCondition
    checkCron: str
    channel: Literal["email", "slack", "webhook"]
    channelConfig: dict = Field(default_factory=dict)


class AlertUpdate(BaseModel):
    condition: AlertCondition | None = None
    checkCron: str | None = None
    enabled: bool | None = None


class AlertRead(AlertCreate):
    id: UUID
    ownerUserId: UUID
    enabled: bool
    lastCheckAt: datetime | None
    lastTriggeredAt: datetime | None
