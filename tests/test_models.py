"""
Unit tests for skill_registry Pydantic models.
Tests Phase 1 (schema) validation: valid inputs pass, invalid inputs raise.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.skill_registry.models.enum import EnumSkill, EnumValue
from backend.skill_registry.models.mutation import Mutation, MutationFile, ValidationRule
from backend.skill_registry.models.pattern import Pattern, PatternFile, PatternParam
from backend.skill_registry.models.skill import DisplayConfig, EntitySkill, FieldDef, FKReference


# ---------------------------------------------------------------------------
# FieldDef tests
# ---------------------------------------------------------------------------


class TestFieldDef:
    def test_valid_field(self):
        f = FieldDef(name="first_name", type="string", label="First Name")
        assert f.name == "first_name"
        assert f.display_label == "First Name"

    def test_name_not_snake_case_raises(self):
        with pytest.raises(ValidationError, match="snake_case"):
            FieldDef(name="FirstName", type="string")

    def test_enum_without_enum_ref_raises(self):
        with pytest.raises(ValidationError, match="enumRef"):
            FieldDef(name="status", type="enum")

    def test_non_enum_with_enum_ref_raises(self):
        with pytest.raises(ValidationError, match="enumRef"):
            FieldDef(name="status", type="string", enumRef="SomeEnum")

    def test_pk_field_not_nullable(self):
        f = FieldDef(name="id", type="uuid", isPK=True, nullable=True)
        # isPK=True means nullable is forced False in entity validation but
        # FieldDef itself allows it; entity validator enforces it
        assert f.isPK is True

    def test_display_label_defaults_to_title_case(self):
        f = FieldDef(name="hire_date", type="date")
        assert f.display_label == "Hire Date"

    def test_sensitive_field(self):
        f = FieldDef(name="salary", type="float", sensitive=True)
        assert f.sensitive is True

    def test_fk_reference(self):
        f = FieldDef(
            name="manager_id",
            type="uuid",
            fk=FKReference(entity="Employee", field="id", display_field="first_name"),
        )
        assert f.fk is not None
        assert f.fk.entity == "Employee"


# ---------------------------------------------------------------------------
# EntitySkill tests
# ---------------------------------------------------------------------------


class TestEntitySkill:
    def _make_minimal_skill(self, **overrides):
        data = {
            "entity": "Employee",
            "table": "employees",
            "adapter": "postgresql",
            "fields": [
                {"name": "id", "type": "uuid", "isPK": True, "nullable": False}
            ],
        }
        data.update(overrides)
        return EntitySkill.model_validate(data)

    def test_valid_skill(self):
        skill = self._make_minimal_skill()
        assert skill.entity == "Employee"
        assert skill.pk_field.name == "id"

    def test_entity_not_pascal_case_raises(self):
        with pytest.raises(ValidationError, match="PascalCase"):
            self._make_minimal_skill(entity="employee")

    def test_table_not_snake_case_raises(self):
        with pytest.raises(ValidationError, match="snake_case"):
            self._make_minimal_skill(table="Employees")

    def test_no_pk_raises(self):
        with pytest.raises(ValidationError, match="primary key"):
            EntitySkill.model_validate({
                "entity": "Employee",
                "table": "employees",
                "adapter": "postgresql",
                "fields": [{"name": "name", "type": "string", "nullable": False}],
            })

    def test_duplicate_field_names_raise(self):
        with pytest.raises(ValidationError, match="Duplicate field"):
            EntitySkill.model_validate({
                "entity": "Employee",
                "table": "employees",
                "adapter": "postgresql",
                "fields": [
                    {"name": "id", "type": "uuid", "isPK": True},
                    {"name": "id", "type": "string"},
                ],
            })

    def test_sensitive_fields_property(self, employee_skill):
        # Employee has salary as sensitive
        sensitive = employee_skill.sensitive_fields
        assert any(f.name == "salary" for f in sensitive)

    def test_get_field(self, employee_skill):
        f = employee_skill.get_field("email")
        assert f is not None
        assert f.type == "string"

    def test_get_field_not_found(self, employee_skill):
        assert employee_skill.get_field("nonexistent") is None


# ---------------------------------------------------------------------------
# EnumSkill tests
# ---------------------------------------------------------------------------


class TestEnumSkill:
    def test_valid_enum(self, employment_type_enum):
        assert employment_type_enum.name == "EmploymentType"
        assert len(employment_type_enum.values) == 4

    def test_active_values_excludes_deprecated(self, employment_type_enum):
        active = employment_type_enum.active_values
        assert all(not v.deprecated for v in active)
        names = [v.value for v in active]
        assert "INTERN" not in names

    def test_is_valid_includes_deprecated(self, employment_type_enum):
        assert employment_type_enum.is_valid("INTERN") is True

    def test_is_valid_false_for_unknown(self, employment_type_enum):
        assert employment_type_enum.is_valid("UNKNOWN") is False

    def test_duplicate_value_raises(self):
        with pytest.raises(ValidationError, match="Duplicate value"):
            EnumSkill.model_validate({
                "name": "Status",
                "values": [
                    {"value": "ACTIVE", "display": "Active"},
                    {"value": "ACTIVE", "display": "Also Active"},
                ],
            })

    def test_value_not_uppercase_raises(self):
        with pytest.raises(ValidationError, match="UPPER_SNAKE_CASE"):
            EnumValue(value="active", display="Active")

    def test_name_not_pascal_case_raises(self):
        with pytest.raises(ValidationError, match="PascalCase"):
            EnumSkill.model_validate({
                "name": "employmentType",
                "values": [{"value": "ACTIVE", "display": "Active"}],
            })

    def test_get_value(self, employment_type_enum):
        v = employment_type_enum.get_value("FULL_TIME")
        assert v is not None
        assert v.display == "Full Time"

    def test_get_value_not_found(self, employment_type_enum):
        assert employment_type_enum.get_value("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# Pattern models tests
# ---------------------------------------------------------------------------


class TestPatternModels:
    def test_valid_pattern(self):
        p = Pattern.model_validate({
            "id": "employee.active",
            "triggers": ["active employees", "show active staff"],
            "query_template": '{}',
        })
        assert p.id == "employee.active"

    def test_pattern_id_invalid_format_raises(self):
        with pytest.raises(ValidationError, match="dot.separated"):
            Pattern.model_validate({
                "id": "Employee.Active",
                "triggers": ["active employees"],
                "query_template": "{}",
            })

    def test_pattern_empty_trigger_raises(self):
        with pytest.raises(ValidationError, match="empty"):
            Pattern.model_validate({
                "id": "employee.active",
                "triggers": ["   "],
                "query_template": "{}",
            })

    def test_duplicate_pattern_ids_raise(self):
        with pytest.raises(ValidationError, match="Duplicate pattern"):
            PatternFile.model_validate({
                "skill_hash": "abc123",
                "entity": "Employee",
                "patterns": [
                    {"id": "employee.active", "triggers": ["active"], "query_template": "{}"},
                    {"id": "employee.active", "triggers": ["other"], "query_template": "{}"},
                ],
            })


# ---------------------------------------------------------------------------
# Mutation models tests
# ---------------------------------------------------------------------------


class TestMutationModels:
    def test_valid_mutation(self):
        m = Mutation.model_validate({
            "id": "employee.create",
            "operation": "create",
            "fields": ["first_name", "last_name"],
        })
        assert m.requires_confirmation is True

    def test_delete_with_fields_raises(self):
        with pytest.raises(ValidationError, match="delete operation"):
            Mutation.model_validate({
                "id": "employee.delete",
                "operation": "delete",
                "fields": ["some_field"],
            })

    def test_duplicate_mutation_ids_raise(self):
        with pytest.raises(ValidationError, match="Duplicate mutation"):
            MutationFile.model_validate({
                "entity": "Employee",
                "mutations": [
                    {"id": "employee.create", "operation": "create"},
                    {"id": "employee.create", "operation": "update"},
                ],
            })
