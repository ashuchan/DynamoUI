"""Schedule CRUD + run-history + test-fire."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.scheduling.models.dtos import (
    DeliveryRunRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from backend.scheduling.models.tables import delivery_runs, schedules
from backend.scheduling.services.cron_parser import next_runs, validate


class ScheduleNotFound(Exception):
    pass


class ScheduleService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list(self, *, owner_id: UUID) -> list[ScheduleRead]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(schedules).where(schedules.c.owner_user_id == owner_id)
                )
            ).mappings().all()
        return [_row_to_read(r) for r in rows]

    async def create(
        self, *, owner_id: UUID, tenant_id: UUID, payload: ScheduleCreate
    ) -> ScheduleRead:
        validate(payload.cronExpr)
        row_id = uuid4()
        next_runs_list = next_runs(payload.cronExpr, tz=payload.timezone, count=5)
        next_run_at = datetime.fromisoformat(next_runs_list[0]) if next_runs_list else None
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(schedules).values(
                    id=row_id,
                    owner_user_id=owner_id,
                    tenant_id=tenant_id,
                    source_type=payload.sourceType,
                    source_id=payload.sourceId,
                    cron_expr=payload.cronExpr,
                    timezone=payload.timezone,
                    channel=payload.channel,
                    channel_config_json=payload.channelConfig,
                    format=payload.format,
                    next_run_at=next_run_at,
                )
            )
        return await self.get(row_id, owner_id=owner_id)

    async def get(self, schedule_id: UUID, *, owner_id: UUID) -> ScheduleRead:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(schedules).where(
                        schedules.c.id == schedule_id,
                        schedules.c.owner_user_id == owner_id,
                    )
                )
            ).mappings().first()
        if row is None:
            raise ScheduleNotFound(str(schedule_id))
        return _row_to_read(row)

    async def update(
        self, schedule_id: UUID, *, owner_id: UUID, payload: ScheduleUpdate
    ) -> ScheduleRead:
        mapped: dict = {}
        if payload.cronExpr is not None:
            validate(payload.cronExpr)
            mapped["cron_expr"] = payload.cronExpr
        if payload.timezone is not None:
            mapped["timezone"] = payload.timezone
        if payload.channelConfig is not None:
            mapped["channel_config_json"] = payload.channelConfig
        if payload.format is not None:
            mapped["format"] = payload.format
        if payload.enabled is not None:
            mapped["enabled"] = payload.enabled
        if mapped:
            mapped["updated_at"] = sa.func.now()
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(schedules)
                    .where(
                        schedules.c.id == schedule_id,
                        schedules.c.owner_user_id == owner_id,
                    )
                    .values(**mapped)
                )
        return await self.get(schedule_id, owner_id=owner_id)

    async def delete(self, schedule_id: UUID, *, owner_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(schedules).where(
                    schedules.c.id == schedule_id,
                    schedules.c.owner_user_id == owner_id,
                )
            )

    async def test_fire(
        self, schedule_id: UUID, *, owner_id: UUID
    ) -> DeliveryRunRead:
        """One-shot manual fire — records a delivery_run and returns it.

        The v1 implementation does not actually dispatch the channel (that's
        wired in the worker). This is intentionally a stub that proves the
        delivery row is created correctly.
        """
        sched = await self.get(schedule_id, owner_id=owner_id)
        run_id = uuid4()
        t0 = time.monotonic()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(delivery_runs).values(
                    id=run_id,
                    schedule_id=sched.id,
                    status="success",
                    latency_ms=int((time.monotonic() - t0) * 1000),
                    rows_delivered=0,
                    finished_at=sa.func.now(),
                )
            )
            row = (
                await conn.execute(
                    sa.select(delivery_runs).where(delivery_runs.c.id == run_id)
                )
            ).mappings().first()
        return _run_to_read(row)

    async def list_runs(
        self,
        schedule_id: UUID,
        *,
        owner_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> dict:
        await self.get(schedule_id, owner_id=owner_id)
        stmt = (
            sa.select(delivery_runs)
            .where(delivery_runs.c.schedule_id == schedule_id)
            .order_by(delivery_runs.c.started_at.desc())
            .limit(limit)
        )
        if before is not None:
            stmt = stmt.where(delivery_runs.c.started_at < before)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        runs = [_run_to_read(r) for r in rows]
        next_cursor = runs[-1].startedAt.isoformat() if len(runs) == limit else None
        return {"runs": [r.model_dump() for r in runs], "nextCursor": next_cursor}


def _row_to_read(row: sa.engine.RowMapping) -> ScheduleRead:
    return ScheduleRead(
        id=row["id"],
        ownerUserId=row["owner_user_id"],
        sourceType=row["source_type"],
        sourceId=row["source_id"],
        cronExpr=row["cron_expr"],
        timezone=row["timezone"],
        channel=row["channel"],
        channelConfig=row["channel_config_json"],
        format=row["format"],
        enabled=row["enabled"],
        lastRunAt=row["last_run_at"],
        nextRunAt=row["next_run_at"],
        nextRuns=next_runs(row["cron_expr"], tz=row["timezone"], count=5),
        failureCount=row["failure_count"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _run_to_read(row: sa.engine.RowMapping) -> DeliveryRunRead:
    return DeliveryRunRead(
        id=row["id"],
        scheduleId=row["schedule_id"],
        alertId=row["alert_id"],
        startedAt=row["started_at"],
        finishedAt=row["finished_at"],
        status=row["status"],
        rowsDelivered=row["rows_delivered"],
        latencyMs=row["latency_ms"],
        errorText=row["error_text"],
    )
