"""
Tests for QueryTranslator — verifies that snake_case logical names map correctly
to PascalCase DB column names (Chinook-style) across joins, aggregations,
group_by, sort, and result_limit.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import dialect as pg_dialect
import pytest

from backend.adapters.postgresql.query_translator import QueryTranslator
from backend.adapters.base import (
    QueryPlan, FilterClause, SortClause, JoinClause, AggregationClause,
)
from backend.skill_registry.models.skill import EntitySkill, FieldDef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table(meta: sa.MetaData, table_name: str, *columns: sa.Column) -> sa.Table:
    """Build an in-memory SA Table with a private MetaData (no engine needed)."""
    return sa.Table(table_name, meta, *columns)


def _make_skill(entity: str, table_logical_name: str, fields: list[dict]) -> EntitySkill:
    """
    Build an EntitySkill.  `table_logical_name` is the snake_case table name
    (satisfies validator).  `db_column_name` on each field handles PascalCase mapping.
    """
    return EntitySkill.model_validate({
        "entity": entity,
        "table": table_logical_name,
        "adapter": "postgresql",
        "fields": [
            {
                "name": f["name"],
                "type": f.get("type", "string"),
                "isPK": f.get("isPK", False),
                "db_column_name": f.get("db_column_name", ""),
            }
            for f in fields
        ],
    })


class _FakeTableBuilder:
    """Returns a pre-built SA Table by entity name (bypasses DB reflection)."""

    def __init__(self, table_map: dict[str, sa.Table]) -> None:
        self._map = table_map

    def build(self, skill: EntitySkill) -> sa.Table:
        return self._map[skill.entity]


class _FakeSkillRegistry:
    def __init__(self, skills: list[EntitySkill]) -> None:
        self.entity_by_name = {s.entity: s for s in skills}


# ---------------------------------------------------------------------------
# Fixtures — Chinook-style tables (PascalCase columns, fresh MetaData each time)
# ---------------------------------------------------------------------------

@pytest.fixture()
def meta():
    return sa.MetaData()


@pytest.fixture()
def album_table(meta):
    return _make_table(
        meta, "Album",
        sa.Column("AlbumId", sa.Integer, primary_key=True),
        sa.Column("Title", sa.String),
        sa.Column("ArtistId", sa.Integer),
    )


@pytest.fixture()
def track_table(meta):
    return _make_table(
        meta, "Track",
        sa.Column("TrackId", sa.Integer, primary_key=True),
        sa.Column("Name", sa.String),
        sa.Column("AlbumId", sa.Integer),
        sa.Column("UnitPrice", sa.Float),
    )


@pytest.fixture()
def invoice_line_table(meta):
    return _make_table(
        meta, "InvoiceLine",
        sa.Column("InvoiceLineId", sa.Integer, primary_key=True),
        sa.Column("TrackId", sa.Integer),
        sa.Column("Quantity", sa.Integer),
        sa.Column("UnitPrice", sa.Float),
    )


@pytest.fixture()
def album_skill():
    return _make_skill("Album", "album", [
        {"name": "album_id",  "type": "integer", "isPK": True, "db_column_name": "AlbumId"},
        {"name": "title",     "type": "string",               "db_column_name": "Title"},
        {"name": "artist_id", "type": "integer",              "db_column_name": "ArtistId"},
    ])


@pytest.fixture()
def track_skill():
    return _make_skill("Track", "track", [
        {"name": "track_id",   "type": "integer", "isPK": True, "db_column_name": "TrackId"},
        {"name": "name",       "type": "string",               "db_column_name": "Name"},
        {"name": "album_id",   "type": "integer",              "db_column_name": "AlbumId"},
        {"name": "unit_price", "type": "float",                "db_column_name": "UnitPrice"},
    ])


@pytest.fixture()
def invoice_line_skill():
    return _make_skill("InvoiceLine", "invoice_line", [
        {"name": "invoice_line_id", "type": "integer", "isPK": True, "db_column_name": "InvoiceLineId"},
        {"name": "track_id",        "type": "integer",              "db_column_name": "TrackId"},
        {"name": "quantity",        "type": "integer",              "db_column_name": "Quantity"},
        {"name": "unit_price",      "type": "float",                "db_column_name": "UnitPrice"},
    ])


@pytest.fixture()
def translator(album_table, track_table, invoice_line_table,
               album_skill, track_skill, invoice_line_skill):
    table_map = {
        "Album": album_table,
        "Track": track_table,
        "InvoiceLine": invoice_line_table,
    }
    registry = _FakeSkillRegistry([album_skill, track_skill, invoice_line_skill])
    return QueryTranslator(
        table_builder=_FakeTableBuilder(table_map),
        skill_registry=registry,
    )


# ---------------------------------------------------------------------------
# _resolve_col
# ---------------------------------------------------------------------------

def test_resolve_col_direct_match(translator, album_table, album_skill):
    """DB column name accessed directly (already PascalCase) resolves."""
    col = translator._resolve_col(album_table, "AlbumId", album_skill)
    assert col is not None
    assert col.key == "AlbumId"


def test_resolve_col_snake_to_pascal(translator, album_table, album_skill):
    """snake_case 'album_id' resolves to 'AlbumId' via db_column_name mapping."""
    col = translator._resolve_col(album_table, "album_id", album_skill)
    assert col is not None
    assert col.key == "AlbumId"


def test_resolve_col_unknown_field_returns_none(translator, album_table, album_skill):
    col = translator._resolve_col(album_table, "nonexistent_field", album_skill)
    assert col is None


def test_resolve_col_empty_field_returns_none(translator, album_table, album_skill):
    col = translator._resolve_col(album_table, "", album_skill)
    assert col is None


def test_resolve_col_null_skill_falls_back_to_direct(translator, album_table):
    """With no skill, direct DB column name still resolves."""
    col = translator._resolve_col(album_table, "AlbumId", None)
    assert col is not None


# ---------------------------------------------------------------------------
# _resolve_any
# ---------------------------------------------------------------------------

def test_resolve_any_primary_table(translator, album_table, track_table,
                                    album_skill, track_skill):
    col = translator._resolve_any(
        "album_id", album_table, album_skill,
        {"Track": track_table}, {"Track": track_skill},
    )
    assert col is not None
    assert col.key == "AlbumId"


def test_resolve_any_joined_table(translator, album_table, track_table,
                                   album_skill, track_skill):
    """'name' is only on Track; _resolve_any should find it in joined tables."""
    col = translator._resolve_any(
        "name", album_table, album_skill,
        {"Track": track_table}, {"Track": track_skill},
    )
    assert col is not None
    assert col.key == "Name"


def test_resolve_any_not_found_returns_none(translator, album_table, album_skill):
    col = translator._resolve_any(
        "totally_missing", album_table, album_skill, {}, {}
    )
    assert col is None


# ---------------------------------------------------------------------------
# build_select — join field resolution
# ---------------------------------------------------------------------------

def test_join_snake_case_resolves(translator, album_skill):
    """
    Album→Track join using snake_case field names on both sides must compile
    without raising (AlbumId on both tables).
    """
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[],
        joins=[JoinClause(
            source_field="album_id",
            target_entity="Track",
            target_field="album_id",
            join_type="inner",
        )],
        aggregations=[],
        group_by=[],
        result_limit=None,
        page=1,
        page_size=25,
    )
    stmt, count_stmt = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "Album" in sql
    assert "Track" in sql


# ---------------------------------------------------------------------------
# build_select — aggregation + group_by snake_case
# ---------------------------------------------------------------------------

def test_group_by_snake_case_resolves(translator, album_skill):
    """group_by=['album_id', 'title'] must map to AlbumId/Title columns."""
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[],
        joins=[JoinClause(
            source_field="album_id",
            target_entity="Track",
            target_field="album_id",
            join_type="inner",
        )],
        aggregations=[AggregationClause(func="count", field="*", alias="track_count")],
        group_by=["album_id", "title"],
        result_limit=None,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "GROUP BY" in sql
    assert "AlbumId" in sql
    assert "Title" in sql


# ---------------------------------------------------------------------------
# build_select — sort by aggregation alias uses literal_column
# ---------------------------------------------------------------------------

def test_sort_by_aggregation_alias(translator, album_skill):
    """
    Sorting by 'purchase_count' (an aggregation alias) must use literal_column,
    not attempt a DB column lookup.
    """
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[SortClause(field="purchase_count", dir="desc")],
        joins=[JoinClause(
            source_field="album_id",
            target_entity="Track",
            target_field="album_id",
            join_type="inner",
        )],
        aggregations=[AggregationClause(func="count", field="track_id", alias="purchase_count")],
        group_by=["album_id", "title"],
        result_limit=5,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "purchase_count" in sql
    assert "DESC" in sql


# ---------------------------------------------------------------------------
# build_select — full top-N query (Album→Track→InvoiceLine)
# ---------------------------------------------------------------------------

def test_top_n_albums_by_purchases(translator, album_skill):
    """
    Reproduces "top 5 albums most purchased":
    Album→Track (album_id→album_id) → InvoiceLine (track_id→track_id).
    Expects LIMIT 5 and ORDER BY purchase_count DESC with SUM aggregation.
    """
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[SortClause(field="purchase_count", dir="desc")],
        joins=[
            JoinClause(
                source_field="album_id",
                target_entity="Track",
                target_field="album_id",
                join_type="inner",
            ),
            JoinClause(
                source_field="track_id",
                target_entity="InvoiceLine",
                target_field="track_id",
                join_type="inner",
            ),
        ],
        aggregations=[AggregationClause(func="sum", field="quantity", alias="purchase_count")],
        group_by=["album_id", "title"],
        result_limit=5,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))

    assert "LIMIT 5" in sql
    assert "sum" in sql.lower()
    assert "purchase_count" in sql
    assert "GROUP BY" in sql
    assert "DESC" in sql
    assert "InvoiceLine" in sql  # multi-hop join reached InvoiceLine


# ---------------------------------------------------------------------------
# build_select — unknown join entity skipped gracefully
# ---------------------------------------------------------------------------

def test_unknown_join_entity_skipped(translator, album_skill):
    """
    A join referencing an entity not in the registry is silently skipped;
    the rest of the query still builds.
    """
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[],
        joins=[JoinClause(
            source_field="album_id",
            target_entity="NonExistentEntity",
            target_field="some_id",
            join_type="inner",
        )],
        aggregations=[],
        group_by=[],
        result_limit=None,
        page=1,
        page_size=25,
    )
    stmt, count_stmt = translator.build_select(album_skill, plan)
    assert stmt is not None
    assert count_stmt is not None


# ---------------------------------------------------------------------------
# build_select — filter snake_case resolution
# ---------------------------------------------------------------------------

def test_filter_snake_case_resolves(translator, album_skill):
    """Filter on 'title' (snake_case) resolves to the 'Title' column."""
    plan = QueryPlan(
        entity="Album",
        filters=[FilterClause(field="title", op="like", value="rock")],
        sort=[],
        joins=[],
        aggregations=[],
        group_by=[],
        result_limit=None,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "Title" in sql
    assert "%" in sql  # ilike wraps value in %…%


def test_unknown_filter_field_skipped(translator, album_skill):
    """Unknown filter field is skipped; query still builds."""
    plan = QueryPlan(
        entity="Album",
        filters=[FilterClause(field="no_such_field", op="eq", value="x")],
        sort=[],
        joins=[],
        aggregations=[],
        group_by=[],
        result_limit=None,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    assert stmt is not None


# ---------------------------------------------------------------------------
# build_select — result_limit vs pagination
# ---------------------------------------------------------------------------

def test_result_limit_applies_limit(translator, album_skill):
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[],
        joins=[],
        aggregations=[],
        group_by=[],
        result_limit=10,
        page=1,
        page_size=25,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "LIMIT" in sql
    assert "10" in sql


def test_pagination_applies_offset(translator, album_skill):
    """Page 3, size 10 → OFFSET 20."""
    plan = QueryPlan(
        entity="Album",
        filters=[],
        sort=[],
        joins=[],
        aggregations=[],
        group_by=[],
        result_limit=None,
        page=3,
        page_size=10,
    )
    stmt, _ = translator.build_select(album_skill, plan)
    sql = str(stmt.compile(dialect=pg_dialect(), compile_kwargs={"literal_binds": True}))
    assert "OFFSET" in sql
    assert "20" in sql
