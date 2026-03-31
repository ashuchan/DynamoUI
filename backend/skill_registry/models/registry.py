"""
In-memory registry models: AdapterRegistry and SkillRegistry.
These are the authoritative indexes used at runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.skill_registry.models.enum import EnumSkill
from backend.skill_registry.models.mutation import MutationFile
from backend.skill_registry.models.pattern import PatternFile
from backend.skill_registry.models.skill import EntitySkill


# ---------------------------------------------------------------------------
# Adapter registry YAML model
# ---------------------------------------------------------------------------


class AdapterEntry(BaseModel):
    """One entry in adapters.registry.yaml."""

    key: str = Field(..., description="Short identifier, e.g. 'postgresql'")
    type: str = Field(..., description="Adapter type, e.g. 'postgresql'")
    host: str = Field("", description="Overridden by DYNAMO_PG_* env vars in production")
    port: int = Field(5432)
    database: str = Field("")
    description: str = Field("")

    @field_validator("key")
    @classmethod
    def _key_snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(f"AdapterEntry.key must be snake_case, got: {v!r}")
        return v


class AdapterRegistry(BaseModel):
    """Parsed adapters.registry.yaml."""

    adapters: list[AdapterEntry] = Field(default_factory=list)

    def get(self, key: str) -> AdapterEntry | None:
        for a in self.adapters:
            if a.key == key:
                return a
        return None

    def keys(self) -> list[str]:
        return [a.key for a in self.adapters]


# ---------------------------------------------------------------------------
# In-memory SkillRegistry — built from loaded YAML files
# ---------------------------------------------------------------------------


@dataclass
class SkillRegistry:
    """
    Runtime index of all loaded skills, enums, patterns, and mutations.
    Built once at startup; read-only thereafter in Phase 1.
    """

    entity_by_name: dict[str, EntitySkill] = field(default_factory=dict)
    enum_by_name: dict[str, EnumSkill] = field(default_factory=dict)
    patterns_by_entity: dict[str, PatternFile] = field(default_factory=dict)
    mutations_by_entity: dict[str, MutationFile] = field(default_factory=dict)
    adapter_registry: AdapterRegistry = field(default_factory=AdapterRegistry)

    # FK graph: {entity_name: [(fk_field_name, target_entity_name, target_field_name)]}
    fk_graph: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    # Boot-time metrics
    boot_time_ms: float = 0.0

    def register_entity(self, skill: EntitySkill) -> None:
        self.entity_by_name[skill.entity] = skill

    def register_enum(self, enum: EnumSkill) -> None:
        self.enum_by_name[enum.name] = enum

    def register_patterns(self, pf: PatternFile) -> None:
        self.patterns_by_entity[pf.entity] = pf

    def register_mutations(self, mf: MutationFile) -> None:
        self.mutations_by_entity[mf.entity] = mf

    def build_fk_graph(self) -> None:
        """Populate self.fk_graph from loaded skills. Called after all entities loaded."""
        self.fk_graph = {}
        for entity_name, skill in self.entity_by_name.items():
            edges: list[tuple[str, str, str]] = []
            for f in skill.fields:
                if f.fk is not None:
                    edges.append((f.name, f.fk.entity, f.fk.field))
            self.fk_graph[entity_name] = edges

    @property
    def entities_loaded(self) -> int:
        return len(self.entity_by_name)

    @property
    def patterns_loaded(self) -> int:
        return sum(len(pf.patterns) for pf in self.patterns_by_entity.values())

    @property
    def enums_loaded(self) -> int:
        return len(self.enum_by_name)

    def all_pattern_ids(self) -> set[str]:
        ids: set[str] = set()
        for pf in self.patterns_by_entity.values():
            for p in pf.patterns:
                ids.add(p.id)
        return ids
