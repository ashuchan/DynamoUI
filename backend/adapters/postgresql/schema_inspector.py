"""
SchemaInspector — inspects a live PostgreSQL database to generate skill YAML scaffolds.
"""
from __future__ import annotations

from typing import Any

import structlog
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger(__name__)


class SchemaInspector:
    """
    Inspects live PostgreSQL table/schema structure for scaffold generation.
    Uses SQLAlchemy reflection — read-only, no schema modifications.
    """

    def __init__(self, adapter_key: str) -> None:
        from backend.adapters.registry import get_adapter
        adapter = get_adapter(adapter_key)
        if adapter is None:
            raise ValueError(f"No adapter registered for key {adapter_key!r}")
        self._engine: AsyncEngine = adapter._engine.read_engine

    async def inspect_table(
        self, table_name: str, schema_name: str = "public"
    ) -> list[dict[str, Any]]:
        """
        Return column descriptors for a single table.
        Each descriptor: {name, type, nullable, is_pk}
        """
        async with self._engine.connect() as conn:
            raw = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns(
                    table_name, schema=schema_name
                )
            )
            pk_cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"]
                    for c in inspect(sync_conn).get_pk_constraint(
                        table_name, schema=schema_name
                    ).get("constrained_columns", [])
                }
            )

        columns = []
        for col in raw:
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "is_pk": col["name"] in pk_cols,
            })

        log.debug(
            "schema_inspector.table_inspected",
            table=table_name,
            schema=schema_name,
            columns=len(columns),
        )
        return columns

    async def list_tables(self, schema_name: str = "public") -> list[str]:
        """Return all table names in the given schema."""
        async with self._engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names(schema=schema_name)
            )
        log.debug(
            "schema_inspector.schema_listed",
            schema=schema_name,
            tables=len(tables),
        )
        return tables
