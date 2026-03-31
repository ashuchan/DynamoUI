"""
Unit tests for yaml_loader — file discovery and parsing.
Uses only files from tests/fixtures/.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.skill_registry.loader.yaml_loader import (
    ParseError,
    discover_all,
    load_enum,
    load_skill,
)


class TestLoadSkill:
    def test_loads_employee_skill(self, employee_skill_path):
        skill = load_skill(employee_skill_path)
        assert skill.entity == "Employee"
        assert skill.table == "employees"
        assert skill.pk_field.name == "id"

    def test_invalid_no_pk_raises(self, skills_dir):
        invalid_path = skills_dir / "invalid_no_pk.skill.yaml"
        with pytest.raises(ParseError) as exc_info:
            load_skill(invalid_path)
        assert "invalid_no_pk" in str(exc_info.value.path)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(Exception):
            load_skill(tmp_path / "nonexistent.skill.yaml")


class TestLoadEnum:
    def test_loads_employment_type(self, employment_type_enum_path):
        enum = load_enum(employment_type_enum_path)
        assert enum.name == "EmploymentType"
        assert len(enum.values) == 4

    def test_loads_department(self, department_enum_path):
        enum = load_enum(department_enum_path)
        assert enum.name == "Department"
        assert any(v.deprecated for v in enum.values)


class TestDiscoverAll:
    def test_discovers_valid_skills_and_enums(self, skills_dir, enums_dir):
        result = discover_all(skills_dir, enums_dir)
        # Should have at least one valid skill
        assert len(result.skills) >= 1
        # Should have some enums
        assert len(result.enums) >= 2
        # Invalid files produce errors but don't abort
        assert len(result.errors) >= 1  # invalid_no_pk.skill.yaml

    def test_discovery_returns_correct_entity_names(self, skills_dir, enums_dir):
        result = discover_all(skills_dir, enums_dir)
        entity_names = [s.entity for _, s in result.skills]
        assert "Employee" in entity_names

    def test_discovery_returns_correct_enum_names(self, skills_dir, enums_dir):
        result = discover_all(skills_dir, enums_dir)
        enum_names = [e.name for _, e in result.enums]
        assert "EmploymentType" in enum_names
        assert "Department" in enum_names

    def test_empty_dirs_return_empty_results(self, tmp_path):
        skills = tmp_path / "skills"
        enums = tmp_path / "enums"
        skills.mkdir()
        enums.mkdir()
        result = discover_all(skills, enums)
        assert result.skills == []
        assert result.enums == []
        assert result.errors == []
