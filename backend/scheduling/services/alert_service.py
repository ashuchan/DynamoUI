"""Alert CRUD + condition evaluation helpers."""
from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.scheduling.models.dtos import (
    AlertCondition,
    AlertCreate,
    AlertRead,
    AlertUpdate,
)
from backend.scheduling.models.tables import alerts
from backend.scheduling.services.cron_parser import validate as validate_cron


class AlertNotFound(Exception):
    pass


class AlertService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list(self, *, owner_id: UUID) -> list[AlertRead]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(alerts).where(alerts.c.owner_user_id == owner_id)
                )
            ).mappings().all()
        return [_row_to_read(r) for r in rows]

    async def create(
        self, *, owner_id: UUID, tenant_id: UUID, payload: AlertCreate
    ) -> AlertRead:
        validate_cron(payload.checkCron)
        row_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(alerts).values(
                    id=row_id,
                    owner_user_id=owner_id,
                    tenant_id=tenant_id,
                    saved_view_id=payload.savedViewId,
                    condition_json=payload.condition.model_dump(),
                    check_cron=payload.checkCron,
                    channel=payload.channel,
                    channel_config_json=payload.channelConfig,
                )
            )
        return await self.get(row_id, owner_id=owner_id)

    async def get(self, alert_id: UUID, *, owner_id: UUID) -> AlertRead:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(alerts).where(
                        alerts.c.id == alert_id,
                        alerts.c.owner_user_id == owner_id,
                    )
                )
            ).mappings().first()
        if row is None:
            raise AlertNotFound(str(alert_id))
        return _row_to_read(row)

    async def update(
        self, alert_id: UUID, *, owner_id: UUID, payload: AlertUpdate
    ) -> AlertRead:
        mapped: dict = {}
        if payload.condition is not None:
            mapped["condition_json"] = payload.condition.model_dump()
        if payload.checkCron is not None:
            validate_cron(payload.checkCron)
            mapped["check_cron"] = payload.checkCron
        if payload.enabled is not None:
            mapped["enabled"] = payload.enabled
        if mapped:
            mapped["updated_at"] = sa.func.now()
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(alerts)
                    .where(
                        alerts.c.id == alert_id,
                        alerts.c.owner_user_id == owner_id,
                    )
                    .values(**mapped)
                )
        return await self.get(alert_id, owner_id=owner_id)

    async def delete(self, alert_id: UUID, *, owner_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(alerts).where(
                    alerts.c.id == alert_id,
                    alerts.c.owner_user_id == owner_id,
                )
            )


def _row_to_read(row: sa.engine.RowMapping) -> AlertRead:
    return AlertRead(
        id=row["id"],
        ownerUserId=row["owner_user_id"],
        savedViewId=row["saved_view_id"],
        condition=AlertCondition(**row["condition_json"]),
        checkCron=row["check_cron"],
        channel=row["channel"],
        channelConfig=row["channel_config_json"],
        enabled=row["enabled"],
        lastCheckAt=row["last_check_at"],
        lastTriggeredAt=row["last_triggered_at"],
    )


def evaluate_condition(condition: AlertCondition, rows: list[dict]) -> bool:
    """Pure function — evaluate a condition against a fetched result set."""
    op_map = {
        "eq": lambda a, b: a == b,
        "ne": lambda a, b: a != b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
    }
    fn = op_map[condition.operator]

    if condition.type == "row_count":
        return fn(len(rows), condition.value)

    if condition.type == "any_row_field":
        assert condition.field is not None
        return any(fn(r.get(condition.field), condition.value) for r in rows)

    if condition.type == "aggregate":
        assert condition.field is not None and condition.aggregate is not None
        values = [r.get(condition.field) for r in rows if r.get(condition.field) is not None]
        if not values:
            return False
        if condition.aggregate == "sum":
            agg = sum(values)
        elif condition.aggregate == "avg":
            agg = sum(values) / len(values)
        elif condition.aggregate == "min":
            agg = min(values)
        else:
            agg = max(values)
        return fn(agg, condition.value)

    return False
