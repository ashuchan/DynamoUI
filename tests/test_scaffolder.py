"""
Unit tests for the scaffolder — column inspection → skill YAML generation.
Only tests the in-memory path (no live DB required).
"""
from __future__ import annotations

import asyncio

import pytest
import yaml

from backend.skill_registry.scaffold.scaffolder import (
    SCAFFOLD_HEADER,
    scaffold_from_columns,
)


def _run(table, schema, adapter, cols):
    """Helper to run the async scaffold_from_columns synchronously in tests."""
    return asyncio.run(scaffold_from_columns(table, schema, adapter, cols))


class TestScaffoldFromColumns:
    def _columns(self):
        return [
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "name", "type": "varchar(255)", "nullable": False, "is_pk": False},
            {"name": "salary", "type": "numeric(10,2)", "nullable": True, "is_pk": False},
            {"name": "hire_date", "type": "timestamp", "nullable": True, "is_pk": False},
            {"name": "is_active", "type": "boolean", "nullable": False, "is_pk": False},
            {"name": "employee_id", "type": "bigint", "nullable": True, "is_pk": False},
        ]

    def _parsed(self, table="employees", schema="public", adapter="postgresql", cols=None):
        cols = cols or self._columns()
        result = _run(table, schema, adapter, cols)
        body = "\n".join(result.skill_yaml.split("\n")[1:])
        return yaml.safe_load(body)

    def test_output_starts_with_scaffold_header(self):
        result = _run("employees", "public", "postgresql", self._columns())
        assert result.skill_yaml.startswith(SCAFFOLD_HEADER)

    def test_output_is_valid_yaml(self):
        parsed = self._parsed()
        assert isinstance(parsed, dict)

    def test_entity_name_pascal_cased(self):
        parsed = self._parsed(table="employee_records")
        assert parsed["entity"] == "EmployeeRecords"

    def test_table_name_preserved(self):
        parsed = self._parsed()
        assert parsed["table"] == "employees"

    def test_adapter_key_set(self):
        parsed = self._parsed()
        assert parsed["adapter"] == "postgresql"

    def test_pk_field_identified(self):
        parsed = self._parsed()
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["id"]["isPK"] is True
        assert fields["name"].get("isPK", False) is False

    def test_type_mapping_string(self):
        parsed = self._parsed(table="t", adapter="pg", cols=[
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "label", "type": "varchar(100)", "nullable": True, "is_pk": False},
        ])
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["label"]["type"] == "string"

    def test_type_mapping_boolean(self):
        parsed = self._parsed(table="t", adapter="pg", cols=[
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "active", "type": "boolean", "nullable": False, "is_pk": False},
        ])
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["active"]["type"] == "boolean"

    def test_type_mapping_date(self):
        parsed = self._parsed(table="t", adapter="pg", cols=[
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "created_at", "type": "timestamp", "nullable": True, "is_pk": False},
        ])
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["created_at"]["type"] == "date"

    def test_columns_visible_limited_to_8(self):
        cols = [
            {"name": f"col_{i}", "type": "string", "nullable": True, "is_pk": i == 0}
            for i in range(15)
        ]
        parsed = self._parsed(table="big_table", adapter="pg", cols=cols)
        assert len(parsed["display"]["columns_visible"]) <= 8
