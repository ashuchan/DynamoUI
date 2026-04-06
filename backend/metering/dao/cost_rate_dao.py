"""
CostRateDAO — append-only access to metering_cost_rates.

Rules enforced here:
- No UPDATE or DELETE methods exist; every change is a new row.
- supersede_active_rate() sets effective_to on the old row and inserts the new one
  in a single transaction.
- change_reason and created_by are required non-blank fields (validated in DTO).
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

import structlog

from backend.metering.dao.base_dao import BaseDAO
from backend.metering.dto.cost_rate_dto import CostRateCreateDTO, CostRateReadDTO
from backend.metering.models.tables import metering_cost_rates

log = structlog.get_logger(__name__)


class CostRateDAO(BaseDAO):
    def __init__(self, write_engine: AsyncEngine) -> None:
        super().__init__(write_engine)

    async def get_active_rate(
        self, provider: str, model: str, on_date: date | None = None
    ) -> CostRateReadDTO | None:
        """
        Return the active cost rate for (provider, model) on the given date.
        Defaults to today when on_date is None.
        """
        target = on_date or date.today()
        stmt = (
            sa.select(metering_cost_rates)
            .where(metering_cost_rates.c.provider == provider)
            .where(metering_cost_rates.c.model == model)
            .where(metering_cost_rates.c.effective_from <= target)
            .where(
                sa.or_(
                    metering_cost_rates.c.effective_to.is_(None),
                    metering_cost_rates.c.effective_to >= target,
                )
            )
            .order_by(metering_cost_rates.c.effective_from.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if row is None:
            return None
        return CostRateReadDTO.model_validate(dict(row))

    async def list_rates(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> list[CostRateReadDTO]:
        """Return all cost rate rows, optionally filtered by provider/model."""
        stmt = sa.select(metering_cost_rates).order_by(
            metering_cost_rates.c.provider,
            metering_cost_rates.c.model,
            metering_cost_rates.c.effective_from.desc(),
        )
        if provider:
            stmt = stmt.where(metering_cost_rates.c.provider == provider)
        if model:
            stmt = stmt.where(metering_cost_rates.c.model == model)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [CostRateReadDTO.model_validate(dict(r)) for r in rows]

    async def supersede_active_rate(self, dto: CostRateCreateDTO) -> CostRateReadDTO:
        """
        Atomically supersede the currently active rate for (provider, model):
        1. Set effective_to = dto.effective_from - 1 day on any active row.
        2. INSERT the new row.
        Returns the newly inserted CostRateReadDTO.
        """
        from datetime import timedelta

        yesterday = dto.effective_from - timedelta(days=1)

        async with self._engine.begin() as conn:
            # Close any active rate for this provider+model
            await conn.execute(
                sa.update(metering_cost_rates)
                .where(metering_cost_rates.c.provider == dto.provider)
                .where(metering_cost_rates.c.model == dto.model)
                .where(metering_cost_rates.c.effective_to.is_(None))
                .values(effective_to=yesterday)
            )

            result = await conn.execute(
                sa.insert(metering_cost_rates)
                .values(
                    provider=dto.provider,
                    model=dto.model,
                    input_cost_per_1k=dto.input_cost_per_1k,
                    output_cost_per_1k=dto.output_cost_per_1k,
                    thinking_cost_per_1k=dto.thinking_cost_per_1k,
                    effective_from=dto.effective_from,
                    effective_to=None,
                    change_reason=dto.change_reason,
                    source_reference=dto.source_reference,
                    created_by=dto.created_by,
                )
                .returning(metering_cost_rates)
            )
            row = result.mappings().first()

        log.info(
            "cost_rate_dao.rate_superseded",
            provider=dto.provider,
            model=dto.model,
            effective_from=str(dto.effective_from),
            new_id=row["id"],
        )
        return CostRateReadDTO.model_validate(dict(row))

    async def insert_initial_rate(self, dto: CostRateCreateDTO) -> CostRateReadDTO:
        """
        Insert a rate row without checking for or closing an existing active row.
        Used only by the Alembic seed migration.
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.insert(metering_cost_rates)
                .values(
                    provider=dto.provider,
                    model=dto.model,
                    input_cost_per_1k=dto.input_cost_per_1k,
                    output_cost_per_1k=dto.output_cost_per_1k,
                    thinking_cost_per_1k=dto.thinking_cost_per_1k,
                    effective_from=dto.effective_from,
                    effective_to=None,
                    change_reason=dto.change_reason,
                    source_reference=dto.source_reference,
                    created_by=dto.created_by,
                )
                .returning(metering_cost_rates)
            )
            row = result.mappings().first()
        return CostRateReadDTO.model_validate(dict(row))
