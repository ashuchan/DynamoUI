"""
Shared pytest fixtures for all DynamoUI backend tests.
All fixtures use files from tests/fixtures/ — no inline YAML data.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Root path for all test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SKILLS_DIR = FIXTURES_DIR / "skills"
ENUMS_DIR = FIXTURES_DIR / "enums"
PATTERNS_DIR = FIXTURES_DIR / "patterns"


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def skills_dir() -> Path:
    return SKILLS_DIR


@pytest.fixture
def enums_dir() -> Path:
    return ENUMS_DIR


@pytest.fixture
def patterns_dir() -> Path:
    return PATTERNS_DIR


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def employee_skill_path() -> Path:
    return SKILLS_DIR / "employee.skill.yaml"


@pytest.fixture
def employment_type_enum_path() -> Path:
    return ENUMS_DIR / "employment_type.enum.yaml"


@pytest.fixture
def department_enum_path() -> Path:
    return ENUMS_DIR / "department.enum.yaml"


# ---------------------------------------------------------------------------
# Parsed model fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def employee_skill(employee_skill_path):
    from backend.skill_registry.loader.yaml_loader import load_skill
    return load_skill(employee_skill_path)


@pytest.fixture
def employment_type_enum(employment_type_enum_path):
    from backend.skill_registry.loader.yaml_loader import load_enum
    return load_enum(employment_type_enum_path)


@pytest.fixture
def department_enum(department_enum_path):
    from backend.skill_registry.loader.yaml_loader import load_enum
    return load_enum(department_enum_path)


@pytest.fixture
def minimal_adapter_registry():
    from backend.skill_registry.models.registry import AdapterEntry, AdapterRegistry
    return AdapterRegistry(
        adapters=[AdapterEntry(key="postgresql", type="postgresql")]
    )


@pytest.fixture
def employee_pattern_file():
    """
    Load the employee patterns fixture, bypassing skill_hash header parsing.
    The fixture file uses a placeholder hash — tests must not rely on hash verification.
    """
    path = PATTERNS_DIR / "employee.patterns.yaml"
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Extract hash from header (or use placeholder)
    skill_hash = "placeholder1234"
    if lines and lines[0].startswith("# skill_hash:"):
        skill_hash = lines[0].split("skill_hash:")[1].strip()
        body_lines = lines[1:]
    else:
        body_lines = lines

    raw = yaml.safe_load("\n".join(body_lines)) or {}
    raw["skill_hash"] = skill_hash

    from backend.skill_registry.models.pattern import PatternFile
    return PatternFile.model_validate(raw)


@pytest.fixture
def built_pattern_cache(employee_pattern_file):
    """Return a PatternCache pre-loaded with the employee patterns fixture."""
    from backend.pattern_cache.cache.pattern_cache import PatternCache
    cache = PatternCache(threshold=0.90, enforce_skill_hash=False)
    cache.build_from_pattern_files([employee_pattern_file])
    return cache


# ---------------------------------------------------------------------------
# Pre-seeded SkillRegistry for API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_registry(employee_skill, employment_type_enum, department_enum):
    """SkillRegistry pre-loaded with employee + enum fixtures."""
    from backend.skill_registry.models.registry import SkillRegistry
    from backend.skill_registry.registry.enum_registry import EnumRegistry

    registry = SkillRegistry()
    registry.register_entity(employee_skill)
    registry.register_enum(employment_type_enum)
    registry.register_enum(department_enum)
    registry.build_fk_graph()

    enum_reg = EnumRegistry()
    enum_reg.register_all(list(registry.enum_by_name.values()))
    registry._enum_registry = enum_reg

    return registry
