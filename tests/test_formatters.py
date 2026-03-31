"""
Unit tests for LLM and UI formatters.
"""
from __future__ import annotations

import pytest


class TestLLMFormatter:
    def test_format_enum_for_llm_includes_all_values(self, employment_type_enum):
        from backend.skill_registry.formatters.llm_formatter import format_enum_for_llm

        output = format_enum_for_llm(employment_type_enum)
        assert "EmploymentType" in output
        assert "FULL_TIME" in output
        assert "CONTRACT" in output
        assert "INTERN" in output

    def test_format_enum_deprecated_flagged(self, employment_type_enum):
        from backend.skill_registry.formatters.llm_formatter import format_enum_for_llm

        output = format_enum_for_llm(employment_type_enum)
        assert "DEPRECATED" in output
        assert "INTERN" in output

    def test_format_entity_excludes_sensitive_fields(self, employee_skill):
        from backend.skill_registry.formatters.llm_formatter import format_entity_for_llm

        output = format_entity_for_llm(employee_skill)
        # salary is sensitive — should not appear as regular field
        assert "SENSITIVE" in output
        # But should not contain the raw salary details
        assert "salary" in output  # field name still appears, but flagged

    def test_format_entity_output_is_plain_text_not_json(self, employee_skill):
        from backend.skill_registry.formatters.llm_formatter import format_entity_for_llm
        import json

        output = format_entity_for_llm(employee_skill)
        # Must not be valid JSON — intentionally plain text
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(output)


class TestUIFormatter:
    def test_format_enum_options_create_mode_excludes_deprecated(
        self, employment_type_enum
    ):
        from backend.skill_registry.formatters.ui_formatter import format_enum_options

        result = format_enum_options(employment_type_enum, mode="create")
        values = [o["value"] for o in result["options"]]
        assert "INTERN" not in values
        assert "FULL_TIME" in values

    def test_format_enum_options_edit_mode_includes_deprecated(
        self, employment_type_enum
    ):
        from backend.skill_registry.formatters.ui_formatter import format_enum_options

        result = format_enum_options(employment_type_enum, mode="edit")
        values = [o["value"] for o in result["options"]]
        assert "INTERN" in values

    def test_format_field_meta_structure(self, employee_skill):
        from backend.skill_registry.formatters.ui_formatter import format_field_meta

        id_field = employee_skill.get_field("id")
        meta = format_field_meta(id_field)
        assert meta["name"] == "id"
        assert meta["isPK"] is True
        assert meta["type"] == "uuid"

    def test_format_display_config(self, employee_skill):
        from backend.skill_registry.formatters.ui_formatter import format_display_config

        config = format_display_config(employee_skill)
        assert config["entity"] == "Employee"
        assert "columnsVisible" in config
        assert config["pageSize"] == 25


class TestEnumRegistry:
    def test_register_and_get(self, employment_type_enum, department_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)
        reg.register(department_enum)

        assert reg.get("EmploymentType") is not None
        assert reg.get("Department") is not None
        assert reg.get("Unknown") is None

    def test_active_options_excludes_deprecated(self, employment_type_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)

        options = reg.active_options("EmploymentType")
        values = [o["value"] for o in options]
        assert "INTERN" not in values

    def test_all_options_includes_deprecated(self, employment_type_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)

        options = reg.all_options("EmploymentType")
        values = [o["value"] for o in options]
        assert "INTERN" in values

    def test_by_group(self, employment_type_enum, department_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)
        reg.register(department_enum)

        hr_enums = reg.by_group("hr")
        names = [e.name for e in hr_enums]
        assert "EmploymentType" in names
        assert "Department" in names

    def test_is_valid_value(self, employment_type_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)

        assert reg.is_valid_value("EmploymentType", "FULL_TIME") is True
        assert reg.is_valid_value("EmploymentType", "INTERN") is True  # deprecated but still valid
        assert reg.is_valid_value("EmploymentType", "UNKNOWN") is False
        assert reg.is_valid_value("NonExistentEnum", "FULL_TIME") is False

    def test_get_or_raise_unknown_raises(self, employment_type_enum):
        from backend.skill_registry.registry.enum_registry import EnumRegistry

        reg = EnumRegistry()
        reg.register(employment_type_enum)

        with pytest.raises(KeyError, match="not found"):
            reg.get_or_raise("Unknown")
