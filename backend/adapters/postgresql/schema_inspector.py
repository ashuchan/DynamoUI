"""
SchemaInspector — inspects a live PostgreSQL database to generate skill YAML scaffolds.
Extracts table/column comments and parses [semantic:X] tags from column comments.
"""
from __future__ import annotations

import re as _re
from typing import Any

import structlog
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

log = structlog.get_logger(__name__)

_SEMANTIC_TAG_RE = _re.compile(r'\[semantic:([^\]]+)\]')
VALID_SEMANTIC_TAGS = frozenset(["time_series", "metric", "status", "identifier", "label"])


def _parse_semantic_tag(comment: str) -> tuple[str, str | None]:
    """
    Extract a [semantic:X] tag from a column comment string.

    Returns:
        (cleaned_comment, semantic_value) for recognised tags — tag stripped from text.
        (comment_unchanged, None) for missing or unrecognised tags.
    """
    match = _SEMANTIC_TAG_RE.search(comment)
    if not match:
        return comment, None
    tag_value = match.group(1).strip()
    if tag_value in VALID_SEMANTIC_TAGS:
        before = comment[:match.start()].rstrip()
        after = comment[match.end():].lstrip()
        cleaned = (before + (" " if before and after else "") + after).strip()
        return cleaned, tag_value
    log.debug("schema_inspector.unknown_semantic_tag", tag=tag_value)
    return comment, None  # preserve comment as-is for unrecognised tags


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
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Return (columns, table_comment) for a single table.

        Each column descriptor: {name, type, nullable, is_pk, comment, semantic}
          - comment: column COMMENT ON COLUMN text (tag stripped if valid semantic found)
          - semantic: parsed semantic tag value, or None
        table_comment: COMMENT ON TABLE text, or empty string.
        """
        async with self._engine.connect() as conn:
            raw = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns(
                    table_name, schema=schema_name
                )
            )
            pk_cols = await conn.run_sync(
                lambda sync_conn: set(
                    inspect(sync_conn).get_pk_constraint(
                        table_name, schema=schema_name
                    ).get("constrained_columns", [])
                )
            )
            table_comment_row = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_comment(
                    table_name, schema=schema_name
                )
            )

        table_comment = ((table_comment_row or {}).get("text") or "").strip()

        columns = []
        for col in raw:
            raw_comment = (col.get("comment") or "").strip()
            comment_text, semantic = _parse_semantic_tag(raw_comment)
            columns.append({
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "is_pk": col["name"] in pk_cols,
                "comment": comment_text,
                "semantic": semantic,
            })

        log.debug(
            "schema_inspector.table_inspected",
            table=table_name,
            schema=schema_name,
            columns=len(columns),
            has_table_comment=bool(table_comment),
        )
        return columns, table_comment

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
