"""
PostgreSQLAdapter — full DataAdapter implementation for PostgreSQL.
Uses asyncpg via SQLAlchemy 2.0 async Core.
"""
from __future__ import annotations

from typing import Any

import structlog

from backend.adapters.base import DataAdapter, MutationPlan, QueryPlan, QueryResult
from backend.adapters.postgresql.diff_builder import DiffBuilder
from backend.adapters.postgresql.engine import PostgreSQLEngine
from backend.adapters.postgresql.mutation_executor import MutationExecutor
from backend.adapters.postgresql.query_translator import QueryTranslator
from backend.adapters.postgresql.schema_validator import SchemaValidator
from backend.adapters.postgresql.table_builder import TableBuilder
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


class PostgreSQLAdapter(DataAdapter):
    """
    PostgreSQL implementation of DataAdapter.
    Handles read queries, single-record fetches, mutation preview/execute, and schema validation.
    """

    def __init__(
        self,
        adapter_key: str = "postgresql",
        settings: object | None = None,
        skill_registry: object | None = None,
    ) -> None:
        from backend.skill_registry.config.settings import pg_settings

        self._adapter_key = adapter_key
        self._settings = settings or pg_settings
        self._skill_registry = skill_registry
        self._engine = PostgreSQLEngine(self._settings)
        self._table_builder = TableBuilder()
        self._translator = QueryTranslator(self._table_builder, self._skill_registry)
        self._diff_builder = DiffBuilder()
        self._executor: MutationExecutor | None = None
        self._validator: SchemaValidator | None = None

    @property
    def adapter_key(self) -> str:
        return self._adapter_key

    async def initialise(self) -> None:
        """Set up DB connection pools. Called once at startup."""
        await self._engine.initialise()
        self._executor = MutationExecutor(
            self._engine.write_engine, self._table_builder
        )
        self._validator = SchemaValidator(self._engine.read_engine)
        log.info("postgresql_adapter.initialised", key=self._adapter_key)

    async def dispose(self) -> None:
        """Release all connection pool resources."""
        await self._engine.dispose()
        log.info("postgresql_adapter.disposed", key=self._adapter_key)

    # ------------------------------------------------------------------
    # DataAdapter interface
    # ------------------------------------------------------------------

    async def execute_query(
        self,
        skill: EntitySkill,
        plan: QueryPlan,
    ) -> QueryResult:
        """
        Execute a read QueryPlan using the read engine (dynamoui_reader).
        Returns rows + total count for pagination.
        """
        import sqlalchemy as sa

        stmt, count_stmt = self._translator.build_select(skill, plan)

        async with self._engine.read_engine.connect() as conn:
            count_result = await conn.execute(count_stmt)
            total_count = count_result.scalar() or 0

            data_result = await conn.execute(stmt)
            field_map = self._db_to_logical(skill)
            rows = [
                self._remap_row(dict(row._mapping), field_map)
                for row in data_result.fetchall()
            ]

        log.debug(
            "postgresql_adapter.query_executed",
            entity=skill.entity,
            rows=len(rows),
            total=total_count,
            page=plan.page,
        )
        return QueryResult(
            rows=rows,
            total_count=total_count,
            page=plan.page,
            page_size=plan.page_size,
        )

    async def fetch_single(
        self,
        skill: EntitySkill,
        pk_value: str,
    ) -> dict[str, Any] | None:
        """Fetch a single record by PK. Returns None if not found."""
        import sqlalchemy as sa

        table = self._table_builder.build(skill)
        pk_col_name = skill.pk_field.db_column_name or skill.pk_field.name
        stmt = sa.select(table).where(table.c[pk_col_name] == pk_value)

        async with self._engine.read_engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            log.debug(
                "postgresql_adapter.single_not_found",
                entity=skill.entity,
                pk=pk_value,
            )
            return None

        field_map = self._db_to_logical(skill)
        record = self._remap_row(dict(row._mapping), field_map)
        log.debug(
            "postgresql_adapter.single_fetched",
            entity=skill.entity,
            pk=pk_value,
        )
        return record

    @staticmethod
    def _db_to_logical(skill: EntitySkill) -> dict[str, str]:
        """Build a map from db_column_name → logical field.name."""
        return {(f.db_column_name or f.name): f.name for f in skill.fields}

    @staticmethod
    def _remap_row(row: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
        """Re-key a row dict from db column names to logical field names."""
        return {field_map.get(k, k): v for k, v in row.items()}

    async def preview_mutation(
        self,
        skill: EntitySkill,
        plan: MutationPlan,
    ) -> dict[str, Any]:
        """
        Build a diff preview in memory.
        For UPDATE/DELETE: fetches the existing record from DB to show before/after.
        Does NOT write to the database.
        """
        if plan.operation == "create":
            return self._diff_builder.build_create_preview(plan, plan.fields)

        elif plan.operation == "update":
            existing = await self.fetch_single(skill, plan.record_pk)
            if existing is None:
                return {
                    "error": f"Record {plan.record_pk!r} not found in {skill.entity}",
                    "operation": "update",
                }
            return self._diff_builder.build_update_preview(plan, existing, plan.fields)

        elif plan.operation == "delete":
            existing = await self.fetch_single(skill, plan.record_pk)
            if existing is None:
                return {
                    "error": f"Record {plan.record_pk!r} not found in {skill.entity}",
                    "operation": "delete",
                }
            return self._diff_builder.build_delete_preview(plan, existing)

        raise ValueError(f"Unknown operation: {plan.operation!r}")

    async def execute_mutation(
        self,
        skill: EntitySkill,
        plan: MutationPlan,
    ) -> dict[str, Any]:
        """Execute a confirmed mutation within a DB transaction."""
        if self._executor is None:
            raise RuntimeError("PostgreSQLAdapter not initialised — call initialise() first")
        return await self._executor.execute(skill, plan)

    async def validate_schema(self, skill: EntitySkill) -> None:
        """Phase 4: validate skill YAML against live DB schema."""
        if self._validator is None:
            raise RuntimeError("PostgreSQLAdapter not initialised — call initialise() first")
        mismatches = await self._validator.validate(skill)
        if mismatches:
            details = "; ".join(f"{m.field}: {m.issue}" for m in mismatches)
            raise ValueError(
                f"Schema mismatch for {skill.entity!r}: {details}"
            )
