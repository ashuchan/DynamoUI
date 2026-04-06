"""
MeteringService — high-level API for recording metering data.

All public methods are fire-and-forget: they catch every exception internally
and log a warning, so metering failures never propagate to the caller.

Callers should:
1. Call start_operation() at the top of the operation.
2. Set a MeteringContext via context.set_metering_context() so the
   MeteringLLMProvider can record interactions automatically.
3. Call complete_operation() in a finally block.

MeteringLLMProvider calls record_llm_interaction() directly — callers do not
need to invoke it themselves.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal
from uuid import UUID

import structlog

from backend.metering.cost import CostCalculator
from backend.metering.dao.cost_rate_dao import CostRateDAO
from backend.metering.dao.interaction_dao import InteractionDAO
from backend.metering.dao.operation_dao import OperationDAO
from backend.metering.dto.interaction_dto import LLMInteractionCreateDTO
from backend.metering.dto.operation_dto import OperationCreateDTO, OperationUpdateDTO

log = structlog.get_logger(__name__)

_ZERO = Decimal("0")


class MeteringService:
    def __init__(
        self,
        operation_dao: OperationDAO,
        interaction_dao: InteractionDAO,
        cost_rate_dao: CostRateDAO,
        cost_calculator: CostCalculator,
    ) -> None:
        self._ops = operation_dao
        self._interactions = interaction_dao
        self._rates = cost_rate_dao
        self._calc = cost_calculator

    # -------------------------------------------------------------------------
    # Operation lifecycle
    # -------------------------------------------------------------------------

    async def start_operation(
        self,
        operation_type: str,
        tenant_id: UUID | None = None,
        session_id: UUID | None = None,
        user_id: str | None = None,
        user_input_hash: str | None = None,
        ip_address: str | None = None,
        metadata: dict | None = None,
    ) -> UUID:
        """
        Insert a metering_operations row and return the new operation_id.
        On failure, logs a warning and returns a random UUID (metering is best-effort).
        """
        operation_id = uuid.uuid4()
        try:
            dto = OperationCreateDTO(
                id=operation_id,
                operation_type=operation_type,
                tenant_id=tenant_id,
                session_id=session_id,
                user_id=user_id,
                user_input_hash=user_input_hash,
                ip_address=ip_address,
                metadata=metadata,
            )
            await self._ops.insert(dto)
            log.debug(
                "metering.operation_started",
                operation_id=str(operation_id),
                operation_type=operation_type,
            )
        except Exception as exc:
            log.warning(
                "metering.start_operation_failed",
                operation_type=operation_type,
                error=str(exc),
            )
        return operation_id

    async def complete_operation(
        self,
        operation_id: UUID,
        dto: OperationUpdateDTO,
    ) -> None:
        """
        Update the operation row with the outcome.
        Always call in a finally block. Swallows all exceptions.
        """
        try:
            await asyncio.shield(self._ops.update_outcome(operation_id, dto))
            log.debug(
                "metering.operation_completed",
                operation_id=str(operation_id),
                success=dto.success,
            )
        except Exception as exc:
            log.warning(
                "metering.complete_operation_failed",
                operation_id=str(operation_id),
                error=str(exc),
            )

    # -------------------------------------------------------------------------
    # LLM interaction recording (called by MeteringLLMProvider)
    # -------------------------------------------------------------------------

    async def record_llm_interaction(
        self,
        dto: LLMInteractionCreateDTO,
    ) -> None:
        """
        Resolve the active cost rate, compute cost_usd, and insert the
        metering_llm_interactions row. Swallows all exceptions.
        """
        try:
            rate = await self._rates.get_active_rate(
                dto.provider, dto.model, on_date=date.today()
            )
            if rate is not None:
                cost_usd = self._calc.compute(
                    rate,
                    prompt_tokens=dto.prompt_tokens,
                    completion_tokens=dto.completion_tokens,
                    thinking_tokens=dto.thinking_tokens,
                )
                cost_rate_id: int | None = rate.id
            else:
                log.warning(
                    "metering.no_cost_rate_found",
                    provider=dto.provider,
                    model=dto.model,
                )
                cost_usd = _ZERO
                cost_rate_id = None

            await self._interactions.insert(dto, cost_usd, cost_rate_id)
            log.debug(
                "metering.llm_interaction_recorded",
                operation_id=str(dto.operation_id),
                provider=dto.provider,
                model=dto.model,
                total_tokens=dto.total_tokens,
                cost_usd=str(cost_usd),
            )
        except Exception as exc:
            log.warning(
                "metering.record_llm_interaction_failed",
                operation_id=str(dto.operation_id),
                error=str(exc),
            )

    # -------------------------------------------------------------------------
    # Cost rate management (used by API routes)
    # -------------------------------------------------------------------------

    async def add_cost_rate(
        self,
        dto: "CostRateCreateDTO",  # noqa: F821
    ) -> "CostRateReadDTO":  # noqa: F821
        """Insert a new cost rate, superseding the current active rate."""
        return await self._rates.supersede_active_rate(dto)

    async def list_cost_rates(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> list["CostRateReadDTO"]:  # noqa: F821
        return await self._rates.list_rates(provider=provider, model=model)

    # -------------------------------------------------------------------------
    # Read / reporting (used by API routes)
    # -------------------------------------------------------------------------

    async def list_operations(
        self,
        operation_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list["OperationReadDTO"]:  # noqa: F821
        return await self._ops.list_operations(
            operation_type=operation_type, page=page, page_size=page_size
        )

    async def get_operation(
        self, operation_id: UUID
    ) -> "OperationReadDTO | None":  # noqa: F821
        return await self._ops.get_by_id(operation_id)

    async def get_interactions_for_operation(
        self, operation_id: UUID
    ) -> list["LLMInteractionReadDTO"]:  # noqa: F821
        return await self._interactions.list_by_operation(operation_id)

    async def cost_by_model(
        self,
        from_ts: "datetime | None" = None,  # noqa: F821
        to_ts: "datetime | None" = None,  # noqa: F821
    ) -> list[dict]:
        return await self._interactions.cost_by_model(from_ts=from_ts, to_ts=to_ts)


def create_metering_service(write_engine: "AsyncEngine") -> MeteringService:  # noqa: F821
    """Factory — wires all DAOs and returns a ready MeteringService."""
    return MeteringService(
        operation_dao=OperationDAO(write_engine),
        interaction_dao=InteractionDAO(write_engine),
        cost_rate_dao=CostRateDAO(write_engine),
        cost_calculator=CostCalculator(),
    )
