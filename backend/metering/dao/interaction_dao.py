"""InteractionDAO — insert and query metering_llm_interactions rows."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.metering.dao.base_dao import BaseDAO
from backend.metering.dto.interaction_dto import LLMInteractionCreateDTO, LLMInteractionReadDTO
from backend.metering.models.tables import metering_llm_interactions


class InteractionDAO(BaseDAO):
    def __init__(self, write_engine: AsyncEngine) -> None:
        super().__init__(write_engine)

    async def insert(
        self,
        dto: LLMInteractionCreateDTO,
        cost_usd: Decimal,
        cost_rate_id: int | None,
    ) -> LLMInteractionReadDTO:
        """Insert a new LLM interaction row and return the persisted DTO."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.insert(metering_llm_interactions)
                .values(
                    id=dto.id,
                    operation_id=dto.operation_id,
                    tenant_id=dto.tenant_id,
                    interaction_type=dto.interaction_type,
                    provider=dto.provider,
                    model=dto.model,
                    prompt_tokens=dto.prompt_tokens,
                    completion_tokens=dto.completion_tokens,
                    thinking_tokens=dto.thinking_tokens,
                    total_tokens=dto.total_tokens,
                    thinking_summary=dto.thinking_summary,
                    cost_usd=cost_usd,
                    cost_rate_id=cost_rate_id,
                    latency_ms=dto.latency_ms,
                    success=dto.success,
                    error_message=dto.error_message,
                )
                .returning(metering_llm_interactions)
            )
            row = result.mappings().first()
        return LLMInteractionReadDTO.model_validate(dict(row))

    async def list_by_operation(
        self, operation_id: UUID
    ) -> list[LLMInteractionReadDTO]:
        """Return all interaction rows for a given operation, oldest first."""
        stmt = (
            sa.select(metering_llm_interactions)
            .where(metering_llm_interactions.c.operation_id == operation_id)
            .order_by(metering_llm_interactions.c.created_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [LLMInteractionReadDTO.model_validate(dict(r)) for r in rows]

    async def cost_by_model(
        self,
        from_ts: "datetime | None" = None,
        to_ts: "datetime | None" = None,
    ) -> list[dict]:
        """
        Aggregate total cost and tokens grouped by (provider, model).
        Returns a list of dicts with keys: provider, model, total_cost_usd,
        total_tokens, interaction_count.
        """
        from datetime import datetime

        stmt = sa.select(
            metering_llm_interactions.c.provider,
            metering_llm_interactions.c.model,
            sa.func.sum(metering_llm_interactions.c.cost_usd).label("total_cost_usd"),
            sa.func.sum(metering_llm_interactions.c.total_tokens).label("total_tokens"),
            sa.func.count(metering_llm_interactions.c.id).label("interaction_count"),
        ).group_by(
            metering_llm_interactions.c.provider,
            metering_llm_interactions.c.model,
        )
        if from_ts:
            stmt = stmt.where(metering_llm_interactions.c.created_at >= from_ts)
        if to_ts:
            stmt = stmt.where(metering_llm_interactions.c.created_at <= to_ts)

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]
