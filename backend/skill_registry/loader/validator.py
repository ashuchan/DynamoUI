"""
4-phase validation pipeline for skill_registry.

Phase 1 — Schema:    Pydantic parse (already done by yaml_loader).
Phase 2 — Cross-Ref: FK targets, enumRefs, adapter keys, pattern file paths, uniqueness.
Phase 3 — Semantic:  Circular FK detection, missing mutations_file warnings, shadowed triggers.
Phase 4 — Connectivity: Live DB schema check (only with --check-connectivity).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator

import structlog

from backend.skill_registry.models.enum import EnumSkill
from backend.skill_registry.models.mutation import MutationFile
from backend.skill_registry.models.pattern import PatternFile
from backend.skill_registry.models.registry import AdapterRegistry, SkillRegistry
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    phase: int
    severity: str  # 'error' | 'warning'
    path: str
    message: str

    def __str__(self) -> str:
        return f"[Phase {self.phase}] {self.severity.upper()} {self.path}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, phase: int, path: str, message: str) -> None:
        self.issues.append(
            ValidationIssue(phase=phase, severity="error", path=path, message=message)
        )

    def add_warning(self, phase: int, path: str, message: str) -> None:
        self.issues.append(
            ValidationIssue(phase=phase, severity="warning", path=path, message=message)
        )

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def summary(self) -> str:
        return (
            f"Validation complete: {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s)"
        )


# ---------------------------------------------------------------------------
# Phase 2 — Cross-reference checks
# ---------------------------------------------------------------------------


def _phase2_cross_reference(
    skills: list[tuple[Path, EntitySkill]],
    enums: list[tuple[Path, EnumSkill]],
    patterns: list[tuple[Path, PatternFile]],
    mutations: list[tuple[Path, MutationFile]],
    adapter_registry: AdapterRegistry,
    result: ValidationResult,
) -> None:
    enum_names = {e.name for _, e in enums}
    entity_names = {s.entity for _, s in skills}
    entity_pk_fields: dict[str, set[str]] = {
        s.entity: {f.name for f in s.fields if f.isPK}
        for _, s in skills
    }
    adapter_keys = set(adapter_registry.keys())
    all_pattern_ids: set[str] = set()

    # Check entity-level uniqueness
    seen_entities: dict[str, Path] = {}
    for path, skill in skills:
        if skill.entity in seen_entities:
            result.add_error(
                2,
                str(path),
                f"Duplicate entity name {skill.entity!r} (also defined in {seen_entities[skill.entity]})",
            )
        seen_entities[skill.entity] = path

    # Check enum-level uniqueness
    seen_enums: dict[str, Path] = {}
    for path, enum in enums:
        if enum.name in seen_enums:
            result.add_error(
                2,
                str(path),
                f"Duplicate enum name {enum.name!r} (also in {seen_enums[enum.name]})",
            )
        seen_enums[enum.name] = path

    # Per-skill field checks
    for path, skill in skills:
        # Adapter key
        if skill.adapter not in adapter_keys:
            result.add_error(
                2,
                str(path),
                f"Entity {skill.entity!r} references adapter {skill.adapter!r} "
                f"which is not in adapters.registry.yaml (known: {sorted(adapter_keys)})",
            )

        for fd in skill.fields:
            # enumRef resolution
            if fd.enumRef and fd.enumRef not in enum_names:
                result.add_error(
                    2,
                    str(path),
                    f"Field {skill.entity}.{fd.name} has enumRef={fd.enumRef!r} "
                    f"but no matching enum found",
                )
            # FK resolution
            if fd.fk is not None:
                target_entity = fd.fk.entity
                target_field = fd.fk.field
                if target_entity not in entity_names:
                    result.add_error(
                        2,
                        str(path),
                        f"Field {skill.entity}.{fd.name} FK target entity "
                        f"{target_entity!r} not found",
                    )
                elif target_field not in entity_pk_fields.get(target_entity, set()):
                    result.add_error(
                        2,
                        str(path),
                        f"Field {skill.entity}.{fd.name} FK target {target_entity}.{target_field} "
                        f"is not a primary key field",
                    )

        # Pattern file path on disk
        if skill.patterns_file:
            pf_path = Path(skill.patterns_file)
            if not pf_path.exists():
                result.add_error(
                    2,
                    str(path),
                    f"Entity {skill.entity!r} patterns_file {skill.patterns_file!r} does not exist",
                )

        # Mutations file path on disk
        if skill.mutations_file:
            mf_path = Path(skill.mutations_file)
            if not mf_path.exists():
                result.add_error(
                    2,
                    str(path),
                    f"Entity {skill.entity!r} mutations_file {skill.mutations_file!r} does not exist",
                )

    # Global pattern ID uniqueness
    for path, pf in patterns:
        for p in pf.patterns:
            if p.id in all_pattern_ids:
                result.add_error(
                    2,
                    str(path),
                    f"Duplicate pattern id {p.id!r} (already registered)",
                )
            all_pattern_ids.add(p.id)

        # Pattern entity must exist
        if pf.entity not in entity_names:
            result.add_error(
                2,
                str(path),
                f"Pattern file references entity {pf.entity!r} which was not found",
            )


# ---------------------------------------------------------------------------
# Phase 3 — Semantic checks
# ---------------------------------------------------------------------------


def _detect_circular_fks(
    entity_names: set[str],
    fk_graph: dict[str, list[tuple[str, str, str]]],
) -> list[list[str]]:
    """Return a list of cycles found in the FK graph. Each cycle is a list of entity names."""
    visited: set[str] = set()
    in_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if node in in_stack:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        in_stack.add(node)
        path.append(node)
        for _, target, _ in fk_graph.get(node, []):
            if target in entity_names:
                dfs(target, path)
        path.pop()
        in_stack.discard(node)

    for entity in entity_names:
        if entity not in visited:
            dfs(entity, [])

    return cycles


def _phase3_semantic(
    skills: list[tuple[Path, EntitySkill]],
    patterns: list[tuple[Path, PatternFile]],
    adapter_registry: AdapterRegistry,
    result: ValidationResult,
    shadow_threshold: float = 0.85,
) -> None:
    entity_names = {s.entity for _, s in skills}
    fk_graph: dict[str, list[tuple[str, str, str]]] = {}
    for _, skill in skills:
        edges = []
        for f in skill.fields:
            if f.fk is not None:
                edges.append((f.name, f.fk.entity, f.fk.field))
        fk_graph[skill.entity] = edges

    # Circular FK detection
    cycles = _detect_circular_fks(entity_names, fk_graph)
    for cycle in cycles:
        result.add_error(
            3,
            "fk_graph",
            f"Circular FK dependency detected: {' -> '.join(cycle)}",
        )

    # Missing mutations_file warnings
    for path, skill in skills:
        if not skill.mutations_file:
            result.add_warning(
                3,
                str(path),
                f"Entity {skill.entity!r} has no mutations_file — will be read-only",
            )

    # Shadowed trigger detection across all patterns
    try:
        from rapidfuzz import fuzz, process as rp
    except ImportError:
        log.warning("validator.rapidfuzz_missing", msg="Skipping shadow trigger check")
        return

    all_triggers: list[tuple[str, str, str]] = []  # (trigger_text, pattern_id, entity)
    for _, pf in patterns:
        for p in pf.patterns:
            for t in p.triggers:
                all_triggers.append((t.lower(), p.id, pf.entity))

    for i, (trigger_i, pid_i, entity_i) in enumerate(all_triggers):
        for j, (trigger_j, pid_j, entity_j) in enumerate(all_triggers):
            if i >= j:
                continue
            if pid_i == pid_j:
                continue
            score = fuzz.token_sort_ratio(trigger_i, trigger_j) / 100.0
            if score >= shadow_threshold:
                result.add_warning(
                    3,
                    pid_i,
                    f"Trigger {trigger_i!r} ({pid_i}) is similar to {trigger_j!r} ({pid_j}) "
                    f"with score {score:.2f} >= {shadow_threshold} — may cause shadowing",
                )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_validation(
    skills: list[tuple[Path, EntitySkill]],
    enums: list[tuple[Path, EnumSkill]],
    patterns: list[tuple[Path, PatternFile]],
    mutations: list[tuple[Path, MutationFile]],
    adapter_registry: AdapterRegistry,
    *,
    shadow_threshold: float = 0.85,
    check_connectivity: bool = False,
    registry: SkillRegistry | None = None,
) -> ValidationResult:
    """
    Run Phase 1–3 validation (Phase 4 only if check_connectivity=True).
    Phase 1 (Schema) is already handled by Pydantic parsing in yaml_loader.
    """
    result = ValidationResult()

    log.info("validator.phase2_start")
    _phase2_cross_reference(skills, enums, patterns, mutations, adapter_registry, result)
    log.info(
        "validator.phase2_complete",
        errors=len(result.errors),
        warnings=len(result.warnings),
    )

    log.info("validator.phase3_start")
    _phase3_semantic(skills, patterns, adapter_registry, result, shadow_threshold)
    log.info(
        "validator.phase3_complete",
        errors=len(result.errors),
        warnings=len(result.warnings),
    )

    if check_connectivity and registry is not None:
        log.info("validator.phase4_start")
        _phase4_connectivity(registry, result)
        log.info("validator.phase4_complete", errors=len(result.errors))

    return result


def _phase4_connectivity(registry: SkillRegistry, result: ValidationResult) -> None:
    """
    Phase 4 — Live DB connectivity check.
    Imports adapter lazily to avoid DB dependency in unit tests.
    """
    import asyncio

    from backend.adapters.registry import get_adapter_registry

    async def _check() -> None:
        adapter_reg = get_adapter_registry()
        for entity_name, skill in registry.entity_by_name.items():
            adapter = adapter_reg.get(skill.adapter)
            if adapter is None:
                result.add_error(
                    4,
                    entity_name,
                    f"No adapter registered for key {skill.adapter!r}",
                )
                continue
            try:
                await adapter.validate_schema(skill)
            except Exception as exc:
                result.add_error(4, entity_name, f"Connectivity check failed: {exc}")

    asyncio.run(_check())
