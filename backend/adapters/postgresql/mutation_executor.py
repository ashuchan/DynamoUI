"""
MutationExecutor — executes INSERT/UPDATE/DELETE operations within a transaction.
All mutations use the write engine (dynamoui_writer).
Automatic rollback on failure.
"""
from __future__ import annotations

from typing import Any

import structlog
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.adapters.base import MutationPlan
from backend.adapters.postgresql.table_builder import TableBuilder
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


class MutationExecutor:
    """
    Executes mutations against PostgreSQL within an atomic transaction.
    Uses parameterised SQLAlchemy Core statements — no raw SQL.
    """

    def __init__(self, write_engine: AsyncEngine, table_builder: TableBuilder) -> None:
        self._engine = write_engine
        self._table_builder = table_builder

    async def execute(
        self,
        skill: EntitySkill,
        plan: MutationPlan,
    ) -> dict[str, Any]:
        """
        Execute the mutation plan within a DB transaction.
        Returns a result dict with success, affected_pk, and error.
        """
        if plan.operation == "create":
            return await self._execute_create(skill, plan)
        elif plan.operation == "update":
            return await self._execute_update(skill, plan)
        elif plan.operation == "delete":
            return await self._execute_delete(skill, plan)
        else:
            raise ValueError(f"Unknown operation: {plan.operation!r}")

    async def _execute_create(
        self, skill: EntitySkill, plan: MutationPlan
    ) -> dict[str, Any]:
        table = self._table_builder.build(skill)
        pk_field = skill.pk_field.name

        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    sa.insert(table).values(**plan.fields).returning(table.c[pk_field])
                )
                row = result.fetchone()
                affected_pk = str(row[0]) if row else None

            log.info(
                "mutation_executor.created",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                pk=affected_pk,
            )
            return {"success": True, "operation": "create", "affected_pk": affected_pk}

        except Exception as exc:
            log.error(
                "mutation_executor.create_failed",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                error=str(exc),
            )
            return {"success": False, "operation": "create", "error": str(exc)}

    async def _execute_update(
        self, skill: EntitySkill, plan: MutationPlan
    ) -> dict[str, Any]:
        table = self._table_builder.build(skill)
        pk_field = skill.pk_field.name

        if not plan.record_pk:
            return {"success": False, "operation": "update", "error": "record_pk required for update"}

        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(table)
                    .where(table.c[pk_field] == plan.record_pk)
                    .values(**plan.fields)
                )

            log.info(
                "mutation_executor.updated",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                pk=plan.record_pk,
            )
            return {"success": True, "operation": "update", "affected_pk": plan.record_pk}

        except Exception as exc:
            log.error(
                "mutation_executor.update_failed",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                pk=plan.record_pk,
                error=str(exc),
            )
            return {"success": False, "operation": "update", "error": str(exc)}

    async def _execute_delete(
        self, skill: EntitySkill, plan: MutationPlan
    ) -> dict[str, Any]:
        table = self._table_builder.build(skill)
        pk_field = skill.pk_field.name

        if not plan.record_pk:
            return {"success": False, "operation": "delete", "error": "record_pk required for delete"}

        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.delete(table).where(table.c[pk_field] == plan.record_pk)
                )

            log.info(
                "mutation_executor.deleted",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                pk=plan.record_pk,
            )
            return {"success": True, "operation": "delete", "affected_pk": plan.record_pk}

        except Exception as exc:
            log.error(
                "mutation_executor.delete_failed",
                entity=skill.entity,
                mutation_id=plan.mutation_id,
                pk=plan.record_pk,
                error=str(exc),
            )
            return {"success": False, "operation": "delete", "error": str(exc)}
