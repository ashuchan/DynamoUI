"""
Unit tests for adapter layer — TableBuilder, QueryTranslator, DiffBuilder.
Integration tests (requiring PostgreSQL) are guarded by pytest.mark.integration.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql

from backend.adapters.base import FilterClause, MutationPlan, QueryPlan, SortClause
from backend.adapters.postgresql.diff_builder import DiffBuilder
from backend.adapters.postgresql.query_translator import QueryTranslator
from backend.adapters.postgresql.table_builder import TableBuilder


# ---------------------------------------------------------------------------
# TableBuilder tests
# ---------------------------------------------------------------------------


class TestTableBuilder:
    def test_builds_table_from_skill(self, employee_skill):
        builder = TableBuilder()
        table = builder.build(employee_skill)
        assert isinstance(table, sa.Table)
        assert table.name == "employees"

    def test_table_has_correct_columns(self, employee_skill):
        builder = TableBuilder()
        table = builder.build(employee_skill)
        col_names = set(table.c.keys())
        expected = {"id", "first_name", "last_name", "email", "employment_type", "department",
                    "salary", "hire_date", "is_active", "manager_id"}
        assert expected == col_names

    def test_pk_column_is_primary_key(self, employee_skill):
        builder = TableBuilder()
        table = builder.build(employee_skill)
        pk_cols = [c.name for c in table.primary_key.columns]
        assert "id" in pk_cols

    def test_sensitive_column_still_in_table(self, employee_skill):
        """Sensitive fields must be queryable — they are masked in logs/LLM, not in DB."""
        builder = TableBuilder()
        table = builder.build(employee_skill)
        assert "salary" in table.c

    def test_no_foreign_key_constraints(self, employee_skill):
        """FK joins resolved at query time — no SA ForeignKey constraints added."""
        builder = TableBuilder()
        table = builder.build(employee_skill)
        for col in table.c:
            assert len(col.foreign_keys) == 0, (
                f"Column {col.name} has FK constraint — DynamoUI must not add FK constraints"
            )

    def test_cached_table_is_same_object(self, employee_skill):
        builder = TableBuilder()
        t1 = builder.build(employee_skill)
        t2 = builder.build(employee_skill)
        assert t1 is t2

    def test_clear_cache(self, employee_skill):
        builder = TableBuilder()
        t1 = builder.build(employee_skill)
        builder.clear_cache()
        t2 = builder.build(employee_skill)
        assert t1 is not t2


# ---------------------------------------------------------------------------
# QueryTranslator tests
# ---------------------------------------------------------------------------


class TestQueryTranslator:
    def _make_translator(self) -> QueryTranslator:
        return QueryTranslator(TableBuilder())

    def test_builds_basic_select(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(entity="Employee")
        stmt, count_stmt = translator.build_select(employee_skill, plan)
        compiled = str(stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "employees" in compiled
        assert "LIMIT" in compiled

    def test_applies_eq_filter(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="is_active", op="eq", value=True)],
        )
        stmt, count_stmt = translator.build_select(employee_skill, plan)
        compiled = str(stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "is_active" in compiled

    def test_applies_like_filter(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="last_name", op="like", value="Smith")],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        compiled = str(stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "last_name" in compiled

    def test_applies_in_filter(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="employment_type", op="in", value=["FULL_TIME", "CONTRACT"])],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        assert stmt is not None

    def test_applies_sort(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            sort=[SortClause(field="last_name", dir="desc")],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        compiled = str(stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "last_name" in compiled
        assert "DESC" in compiled.upper()

    def test_pagination(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(entity="Employee", page=3, page_size=10)
        stmt, _ = translator.build_select(employee_skill, plan)
        compiled = str(stmt.compile(dialect=sa.dialects.postgresql.dialect()))
        assert "OFFSET" in compiled.upper()
        assert "LIMIT" in compiled.upper()

    def test_unknown_filter_field_ignored(self, employee_skill):
        """Unknown filter fields should be silently ignored (logged as warning)."""
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="nonexistent_field", op="eq", value="x")],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        assert stmt is not None

    def test_unknown_filter_op_ignored(self, employee_skill):
        translator = self._make_translator()
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="last_name", op="unknown_op", value="x")],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        assert stmt is not None

    def test_no_string_concatenation_in_filters(self, employee_skill):
        """Verify filters use parameterised queries, not string concatenation."""
        translator = self._make_translator()
        malicious_value = "'; DROP TABLE employees; --"
        plan = QueryPlan(
            entity="Employee",
            filters=[FilterClause(field="last_name", op="eq", value=malicious_value)],
        )
        stmt, _ = translator.build_select(employee_skill, plan)
        # The value should appear as a parameter, not in the compiled SQL string
        compiled_str = str(stmt.compile(
            dialect=sa.dialects.postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        ))
        assert "DROP TABLE" not in compiled_str


# ---------------------------------------------------------------------------
# DiffBuilder tests
# ---------------------------------------------------------------------------


class TestDiffBuilder:
    def _make_plan(self, operation="create", pk=None) -> MutationPlan:
        return MutationPlan(
            entity="Employee",
            mutation_id=f"employee.{operation}",
            operation=operation,
            record_pk=pk,
            fields={"first_name": "Alice", "last_name": "Smith"},
        )

    def test_create_preview_structure(self):
        builder = DiffBuilder()
        plan = self._make_plan(operation="create")
        result = builder.build_create_preview(plan, plan.fields)
        assert result["operation"] == "create"
        assert result["entity"] == "Employee"
        assert "diff" in result
        for row in result["diff"]:
            assert row["before"] is None
            assert row["after"] is not None

    def test_update_preview_shows_only_changed_fields(self):
        builder = DiffBuilder()
        plan = self._make_plan(operation="update", pk="uuid-123")
        existing = {"first_name": "Alice", "last_name": "Jones", "email": "a@example.com"}
        proposed = {"first_name": "Alice", "last_name": "Smith"}  # last_name changed
        result = builder.build_update_preview(plan, existing, proposed)
        changed_fields = [r["field"] for r in result["diff"]]
        assert "last_name" in changed_fields
        # first_name unchanged — should not appear
        assert "first_name" not in changed_fields

    def test_delete_preview_shows_all_fields(self):
        builder = DiffBuilder()
        plan = self._make_plan(operation="delete", pk="uuid-123")
        existing = {"id": "uuid-123", "first_name": "Alice", "last_name": "Smith"}
        result = builder.build_delete_preview(plan, existing)
        assert result["operation"] == "delete"
        field_names = [r["field"] for r in result["diff"]]
        assert "id" in field_names
        for row in result["diff"]:
            assert row["after"] is None
            assert row["before"] is not None


# ---------------------------------------------------------------------------
# TypeMap tests
# ---------------------------------------------------------------------------


class TestTypeMap:
    def test_string_with_max_length(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("string", max_length=100)
        assert isinstance(t, sa.String)

    def test_string_without_max_length_is_text(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("string")
        assert isinstance(t, sa.Text)

    def test_integer_pk_is_biginteger(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("integer", is_pk=True)
        assert isinstance(t, sa.BigInteger)

    def test_integer_non_pk_is_integer(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("integer")
        assert isinstance(t, sa.Integer)

    def test_uuid_type(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("uuid")
        assert isinstance(t, sa.UUID)

    def test_boolean_type(self):
        from backend.adapters.postgresql.type_map import get_column_type

        t = get_column_type("boolean")
        assert isinstance(t, sa.Boolean)
