"""
TableBuilder — converts skill YAML FieldDef objects into SQLAlchemy Table objects.

CRITICAL: FK joins are resolved at query time using the FK graph.
Do NOT use sa.ForeignKey constraints — DynamoUI does not own the tables.
"""
from __future__ import annotations

import structlog
import sqlalchemy as sa
from sqlalchemy import MetaData

from backend.adapters.postgresql.type_map import get_column_type
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


class TableBuilder:
    """
    Builds sa.Table objects from EntitySkill definitions.
    Each call to build() returns a new Table registered in the provided MetaData.
    No foreign key constraints are added — see class docstring.
    """

    def __init__(self) -> None:
        self._metadata = MetaData()
        self._cache: dict[str, sa.Table] = {}

    def build(self, skill: EntitySkill) -> sa.Table:
        """
        Build (or return cached) sa.Table for the given skill.
        Schema is set from skill.schema_name.
        """
        cache_key = f"{skill.schema_name}.{skill.db_table_name or skill.table}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        columns = []
        for field in skill.fields:
            col_type = get_column_type(
                field.type,
                max_length=field.max_length,
                is_pk=field.isPK,
            )
            col = sa.Column(
                field.db_column_name or field.name,
                col_type,
                primary_key=field.isPK,
                nullable=field.nullable if not field.isPK else False,
            )
            columns.append(col)

        table = sa.Table(
            skill.db_table_name or skill.table,
            self._metadata,
            *columns,
            schema=skill.schema_name if skill.schema_name != "public" else None,
        )

        self._cache[cache_key] = table
        log.debug(
            "table_builder.built",
            entity=skill.entity,
            table=skill.table,
            schema=skill.schema_name,
            columns=len(columns),
        )
        return table

    def clear_cache(self) -> None:
        """Clear all cached tables (e.g. after schema reload)."""
        self._cache.clear()
        self._metadata = MetaData()
