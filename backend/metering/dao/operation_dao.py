"""OperationDAO — insert and update metering_operations rows."""
from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

import structlog

from backend.metering.dao.base_dao import BaseDAO
from backend.metering.dto.operation_dto import (
    OperationCreateDTO,
    OperationReadDTO,
    OperationUpdateDTO,
)
from backend.metering.models.tables import metering_operations

log = structlog.get_logger(__name__)


class OperationDAO(BaseDAO):
    def __init__(self, write_engine: AsyncEngine) -> None:
        super().__init__(write_engine)

    async def insert(self, dto: OperationCreateDTO) -> OperationReadDTO:
        """Insert a new operation row and return the persisted DTO."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.insert(metering_operations)
                .values(
                    id=dto.id,
                    tenant_id=dto.tenant_id,
                    session_id=dto.session_id,
                    user_id=dto.user_id,
                    operation_type=dto.operation_type,
                    user_input_hash=dto.user_input_hash,
                    ip_address=dto.ip_address,
                    metadata=dto.metadata,
                    # outcome fields default to NULL / TRUE until complete_operation()
                )
                .returning(metering_operations)
            )
            row = result.mappings().first()
        return OperationReadDTO.model_validate(dict(row))

    async def update_outcome(
        self, operation_id: UUID, dto: OperationUpdateDTO
    ) -> None:
        """
        Update a row with the outcome of the operation.
        Only non-None fields in dto are written.
        """
        values = {k: v for k, v in dto.model_dump().items() if v is not None}
        # success=False must still be written even though it's falsy
        if "success" in dto.model_fields_set:
            values["success"] = dto.success

        if not values:
            return

        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(metering_operations)
                .where(metering_operations.c.id == operation_id)
                .values(**values)
            )

    async def get_by_id(self, operation_id: UUID) -> OperationReadDTO | None:
        """Fetch a single operation row by primary key."""
        stmt = sa.select(metering_operations).where(
            metering_operations.c.id == operation_id
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        if row is None:
            return None
        return OperationReadDTO.model_validate(dict(row))

    async def list_operations(
        self,
        operation_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[OperationReadDTO]:
        """Paginated list of operations, newest first."""
        stmt = sa.select(metering_operations).order_by(
            metering_operations.c.created_at.desc()
        )
        if operation_type:
            stmt = stmt.where(metering_operations.c.operation_type == operation_type)
        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [OperationReadDTO.model_validate(dict(r)) for r in rows]
