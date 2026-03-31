"""
SchemaValidator — Phase 4 validation: compares skill YAML against live DB schema.
Only runs with `dynamoui validate --check-connectivity`.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


@dataclass
class SchemaMismatch:
    field: str
    issue: str


class SchemaValidator:
    """
    Validates that an EntitySkill definition matches the live PostgreSQL table structure.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def validate(self, skill: EntitySkill) -> list[SchemaMismatch]:
        """
        Compare skill fields against the live table columns.
        Returns a list of mismatches (empty list = valid).
        """
        mismatches: list[SchemaMismatch] = []

        try:
            async with self._engine.connect() as conn:
                live_cols = await conn.run_sync(
                    lambda sync_conn: {
                        col["name"]: col
                        for col in inspect(sync_conn).get_columns(
                            skill.table, schema=skill.schema_name
                        )
                    }
                )
        except Exception as exc:
            log.error(
                "schema_validator.connection_failed",
                entity=skill.entity,
                table=skill.table,
                error=str(exc),
            )
            return [SchemaMismatch(field="<connection>", issue=str(exc))]

        skill_field_names = {f.name for f in skill.fields}
        live_col_names = set(live_cols.keys())

        # Fields in skill but not in DB
        for missing in skill_field_names - live_col_names:
            mismatches.append(
                SchemaMismatch(
                    field=missing,
                    issue=f"Field {missing!r} defined in skill YAML but not found in table {skill.table!r}",
                )
            )

        # Columns in DB but not in skill (warning only — not blocking)
        for extra in live_col_names - skill_field_names:
            log.warning(
                "schema_validator.unmapped_column",
                entity=skill.entity,
                column=extra,
            )

        if not mismatches:
            log.info(
                "schema_validator.ok",
                entity=skill.entity,
                table=skill.table,
                fields=len(skill.fields),
            )

        return mismatches
