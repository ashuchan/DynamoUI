"""
Unit tests for the scaffolder — column inspection → skill YAML generation.
Only tests the in-memory path (no live DB required).
"""
from __future__ import annotations

import pytest
import yaml

from backend.skill_registry.scaffold.scaffolder import (
    SCAFFOLD_HEADER,
    scaffold_from_columns,
)


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

    def test_output_starts_with_scaffold_header(self):
        output = scaffold_from_columns("employees", "public", "postgresql", self._columns())
        assert output.startswith(SCAFFOLD_HEADER)

    def test_output_is_valid_yaml(self):
        output = scaffold_from_columns("employees", "public", "postgresql", self._columns())
        # Strip the header comment and parse
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        assert isinstance(parsed, dict)

    def test_entity_name_pascal_cased(self):
        output = scaffold_from_columns("employee_records", "public", "postgresql", self._columns())
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        assert parsed["entity"] == "EmployeeRecords"

    def test_table_name_preserved(self):
        output = scaffold_from_columns("employees", "public", "postgresql", self._columns())
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        assert parsed["table"] == "employees"

    def test_adapter_key_set(self):
        output = scaffold_from_columns("employees", "public", "postgresql", self._columns())
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        assert parsed["adapter"] == "postgresql"

    def test_pk_field_identified(self):
        output = scaffold_from_columns("employees", "public", "postgresql", self._columns())
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["id"]["isPK"] is True
        assert fields["name"].get("isPK", False) is False

    def test_type_mapping_string(self):
        output = scaffold_from_columns("t", "public", "pg", [
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "label", "type": "varchar(100)", "nullable": True, "is_pk": False},
        ])
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["label"]["type"] == "string"

    def test_type_mapping_boolean(self):
        output = scaffold_from_columns("t", "public", "pg", [
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "active", "type": "boolean", "nullable": False, "is_pk": False},
        ])
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["active"]["type"] == "boolean"

    def test_type_mapping_date(self):
        output = scaffold_from_columns("t", "public", "pg", [
            {"name": "id", "type": "uuid", "nullable": False, "is_pk": True},
            {"name": "created_at", "type": "timestamp", "nullable": True, "is_pk": False},
        ])
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        fields = {f["name"]: f for f in parsed["fields"]}
        assert fields["created_at"]["type"] == "date"

    def test_columns_visible_limited_to_8(self):
        cols = [
            {"name": f"col_{i}", "type": "string", "nullable": True, "is_pk": i == 0}
            for i in range(15)
        ]
        output = scaffold_from_columns("big_table", "public", "pg", cols)
        body = "\n".join(output.split("\n")[1:])
        parsed = yaml.safe_load(body)
        assert len(parsed["display"]["columns_visible"]) <= 8
