"""
Unit tests for the 4-phase validation pipeline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.skill_registry.loader.validator import run_validation
from backend.skill_registry.loader.yaml_loader import load_enum, load_skill
from backend.skill_registry.models.registry import AdapterEntry, AdapterRegistry


def _make_adapter_registry(keys=("postgresql",)) -> AdapterRegistry:
    return AdapterRegistry(
        adapters=[AdapterEntry(key=k, type=k) for k in keys]
    )


class TestPhase2CrossReference:
    def test_unknown_adapter_key_produces_error(self, employee_skill):
        """Skill references adapter not in registry → Phase 2 error."""
        result = run_validation(
            skills=[(Path("dummy.yaml"), employee_skill)],
            enums=[],
            patterns=[],
            mutations=[],
            adapter_registry=_make_adapter_registry(keys=("mysql",)),
        )
        assert result.has_errors
        errors = [i for i in result.errors if "adapter" in i.message.lower()]
        assert errors

    def test_unresolved_enum_ref_produces_error(self, skills_dir, enums_dir):
        """Skill with enumRef to unknown enum → Phase 2 error."""
        from backend.skill_registry.loader.yaml_loader import load_skill
        invalid_path = skills_dir / "invalid_bad_enum_ref.skill.yaml"
        skill = load_skill(invalid_path)
        adapter_reg = _make_adapter_registry()

        result = run_validation(
            skills=[(invalid_path, skill)],
            enums=[],
            patterns=[],
            mutations=[],
            adapter_registry=adapter_reg,
        )
        assert result.has_errors
        errors = [i for i in result.errors if "enumRef" in i.message or "enum" in i.message.lower()]
        assert errors

    def test_valid_skill_with_matching_enum_passes(
        self, employee_skill, employment_type_enum, department_enum
    ):
        adapter_reg = _make_adapter_registry()
        result = run_validation(
            skills=[(Path("employee.skill.yaml"), employee_skill)],
            enums=[
                (Path("employment_type.enum.yaml"), employment_type_enum),
                (Path("department.enum.yaml"), department_enum),
            ],
            patterns=[],
            mutations=[],
            adapter_registry=adapter_reg,
        )
        assert not result.has_errors

    def test_duplicate_entity_names_produce_error(
        self, employee_skill, employment_type_enum, department_enum
    ):
        adapter_reg = _make_adapter_registry()
        result = run_validation(
            skills=[
                (Path("a.yaml"), employee_skill),
                (Path("b.yaml"), employee_skill),
            ],
            enums=[
                (Path("employment_type.enum.yaml"), employment_type_enum),
                (Path("department.enum.yaml"), department_enum),
            ],
            patterns=[],
            mutations=[],
            adapter_registry=adapter_reg,
        )
        assert result.has_errors
        assert any("Duplicate entity" in i.message for i in result.errors)

    def test_duplicate_enum_names_produce_error(self, employment_type_enum):
        result = run_validation(
            skills=[],
            enums=[
                (Path("a.enum.yaml"), employment_type_enum),
                (Path("b.enum.yaml"), employment_type_enum),
            ],
            patterns=[],
            mutations=[],
            adapter_registry=_make_adapter_registry(),
        )
        assert result.has_errors
        assert any("Duplicate enum" in i.message for i in result.errors)


class TestPhase3Semantic:
    def test_no_mutations_file_is_warning(self, employee_skill, employment_type_enum, department_enum):
        adapter_reg = _make_adapter_registry()
        # Employee skill has empty mutations_file
        result = run_validation(
            skills=[(Path("employee.skill.yaml"), employee_skill)],
            enums=[
                (Path("employment_type.enum.yaml"), employment_type_enum),
                (Path("department.enum.yaml"), department_enum),
            ],
            patterns=[],
            mutations=[],
            adapter_registry=adapter_reg,
        )
        # Should be a warning, not an error
        assert not result.has_errors
        warnings = [w for w in result.warnings if "read-only" in w.message]
        assert warnings

    def test_circular_fk_produces_error(self):
        """Two entities with circular FK references → Phase 3 error."""
        from backend.skill_registry.models.skill import EntitySkill, FKReference

        skill_a = EntitySkill.model_validate({
            "entity": "SkillA",
            "table": "skill_a",
            "adapter": "postgresql",
            "fields": [
                {"name": "id", "type": "uuid", "isPK": True},
                {"name": "skill_b_id", "type": "uuid", "fk": {"entity": "SkillB", "field": "id"}},
            ],
        })
        skill_b = EntitySkill.model_validate({
            "entity": "SkillB",
            "table": "skill_b",
            "adapter": "postgresql",
            "fields": [
                {"name": "id", "type": "uuid", "isPK": True},
                {"name": "skill_a_id", "type": "uuid", "fk": {"entity": "SkillA", "field": "id"}},
            ],
        })
        result = run_validation(
            skills=[(Path("a.yaml"), skill_a), (Path("b.yaml"), skill_b)],
            enums=[],
            patterns=[],
            mutations=[],
            adapter_registry=_make_adapter_registry(),
        )
        assert result.has_errors
        assert any("Circular" in i.message for i in result.errors)


class TestValidationResult:
    def test_summary_string(self, employee_skill):
        adapter_reg = _make_adapter_registry()
        result = run_validation(
            skills=[(Path("e.yaml"), employee_skill)],
            enums=[],
            patterns=[],
            mutations=[],
            adapter_registry=adapter_reg,
        )
        summary = result.summary()
        assert "error" in summary
        assert "warning" in summary
