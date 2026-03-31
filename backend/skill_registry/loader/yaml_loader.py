"""
YAML file discovery + parsing for skill_registry.
Discovers *.skill.yaml, *.enum.yaml, *.patterns.yaml, *.mutations.yaml files.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

import structlog
import yaml
from pydantic import ValidationError

from backend.skill_registry.models.enum import EnumSkill
from backend.skill_registry.models.mutation import MutationFile
from backend.skill_registry.models.pattern import PatternFile
from backend.skill_registry.models.registry import AdapterRegistry
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Parse errors
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """Raised when a YAML file fails to parse or validate."""

    def __init__(self, path: Path, cause: Exception) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"Failed to parse {path}: {cause}")


# ---------------------------------------------------------------------------
# Discovery results
# ---------------------------------------------------------------------------


class DiscoveryResult(NamedTuple):
    skills: list[tuple[Path, EntitySkill]]
    enums: list[tuple[Path, EnumSkill]]
    patterns: list[tuple[Path, PatternFile]]
    mutations: list[tuple[Path, MutationFile]]
    errors: list[ParseError]


# ---------------------------------------------------------------------------
# Low-level loaders
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return its contents as a dict."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ParseError(path, exc) from exc


def _parse_pattern_file_with_hash(path: Path) -> PatternFile:
    """
    Parse a *.patterns.yaml file.
    The first line must be: # skill_hash: <16-char-hash>
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or not lines[0].startswith("# skill_hash:"):
        raise ParseError(
            path,
            ValueError(
                "Pattern file must start with '# skill_hash: <16-char-hash>'"
            ),
        )
    skill_hash = lines[0].split("skill_hash:")[1].strip()

    try:
        raw = yaml.safe_load("\n".join(lines[1:])) or {}
    except yaml.YAMLError as exc:
        raise ParseError(path, exc) from exc

    raw["skill_hash"] = skill_hash
    try:
        return PatternFile.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(path, exc) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_skill(path: Path) -> EntitySkill:
    """Parse a single *.skill.yaml file."""
    raw = _load_yaml(path)
    try:
        skill = EntitySkill.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(path, exc) from exc
    log.debug("skill.loaded", path=str(path), entity=skill.entity)
    return skill


def load_enum(path: Path) -> EnumSkill:
    """Parse a single *.enum.yaml file."""
    raw = _load_yaml(path)
    try:
        enum = EnumSkill.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(path, exc) from exc
    log.debug("enum.loaded", path=str(path), name=enum.name)
    return enum


def load_patterns(path: Path) -> PatternFile:
    """Parse a single *.patterns.yaml file."""
    pf = _parse_pattern_file_with_hash(path)
    log.debug(
        "patterns.loaded",
        path=str(path),
        entity=pf.entity,
        count=len(pf.patterns),
    )
    return pf


def load_mutations(path: Path) -> MutationFile:
    """Parse a single *.mutations.yaml file."""
    raw = _load_yaml(path)
    try:
        mf = MutationFile.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(path, exc) from exc
    log.debug(
        "mutations.loaded",
        path=str(path),
        entity=mf.entity,
        count=len(mf.mutations),
    )
    return mf


def load_adapter_registry(path: Path) -> AdapterRegistry:
    """Parse adapters.registry.yaml."""
    raw = _load_yaml(path)
    try:
        return AdapterRegistry.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(path, exc) from exc


def discover_all(
    skills_dir: Path,
    enums_dir: Path,
    *,
    patterns_dirs: list[Path] | None = None,
    mutations_dirs: list[Path] | None = None,
) -> DiscoveryResult:
    """
    Walk the given directories and parse all recognised YAML files.
    Errors are collected and returned — they do not raise immediately.
    The caller decides whether to abort on errors.
    """
    skills: list[tuple[Path, EntitySkill]] = []
    enums: list[tuple[Path, EnumSkill]] = []
    patterns: list[tuple[Path, PatternFile]] = []
    mutations: list[tuple[Path, MutationFile]] = []
    errors: list[ParseError] = []

    # Skills
    for path in sorted(skills_dir.glob("*.skill.yaml")):
        try:
            skills.append((path, load_skill(path)))
        except ParseError as exc:
            log.warning("yaml_loader.skill_parse_error", path=str(path), error=str(exc))
            errors.append(exc)

    # Enums
    for path in sorted(enums_dir.glob("*.enum.yaml")):
        try:
            enums.append((path, load_enum(path)))
        except ParseError as exc:
            log.warning("yaml_loader.enum_parse_error", path=str(path), error=str(exc))
            errors.append(exc)

    # Patterns (search skills_dir by default)
    _pattern_dirs = patterns_dirs or [skills_dir]
    for search_dir in _pattern_dirs:
        for path in sorted(search_dir.glob("*.patterns.yaml")):
            try:
                patterns.append((path, load_patterns(path)))
            except ParseError as exc:
                log.warning(
                    "yaml_loader.pattern_parse_error", path=str(path), error=str(exc)
                )
                errors.append(exc)

    # Mutations
    _mutation_dirs = mutations_dirs or [skills_dir]
    for search_dir in _mutation_dirs:
        for path in sorted(search_dir.glob("*.mutations.yaml")):
            try:
                mutations.append((path, load_mutations(path)))
            except ParseError as exc:
                log.warning(
                    "yaml_loader.mutation_parse_error", path=str(path), error=str(exc)
                )
                errors.append(exc)

    log.info(
        "yaml_loader.discovery_complete",
        skills=len(skills),
        enums=len(enums),
        patterns=len(patterns),
        mutations=len(mutations),
        errors=len(errors),
    )
    return DiscoveryResult(skills, enums, patterns, mutations, errors)
