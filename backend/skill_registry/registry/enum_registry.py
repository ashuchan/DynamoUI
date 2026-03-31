"""
EnumRegistry — in-memory index of all loaded EnumSkill objects.
Provides query methods used by formatters, validators, and the API layer.
"""
from __future__ import annotations

import structlog

from backend.skill_registry.models.enum import EnumSkill, EnumValue

log = structlog.get_logger(__name__)


class EnumRegistry:
    """
    Fast in-memory lookup for EnumSkill objects.
    Built once at startup from the SkillRegistry.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, EnumSkill] = {}
        self._by_group: dict[str, list[EnumSkill]] = {}

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def register(self, enum: EnumSkill) -> None:
        """Add or replace an enum by name."""
        if enum.name in self._by_name:
            log.warning("enum_registry.duplicate", name=enum.name)
        self._by_name[enum.name] = enum
        if enum.group:
            self._by_group.setdefault(enum.group, []).append(enum)
        log.debug("enum_registry.registered", name=enum.name, values=len(enum.values))

    def register_all(self, enums: list[EnumSkill]) -> None:
        for enum in enums:
            self.register(enum)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, name: str) -> EnumSkill | None:
        return self._by_name.get(name)

    def get_or_raise(self, name: str) -> EnumSkill:
        enum = self._by_name.get(name)
        if enum is None:
            raise KeyError(f"Enum {name!r} not found in registry")
        return enum

    def all(self) -> list[EnumSkill]:
        return list(self._by_name.values())

    def all_names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def by_group(self, group: str) -> list[EnumSkill]:
        return list(self._by_group.get(group, []))

    def groups(self) -> list[str]:
        return sorted(self._by_group.keys())

    def is_valid_value(self, enum_name: str, value: str) -> bool:
        """Return True if *value* exists in the named enum (including deprecated)."""
        enum = self._by_name.get(enum_name)
        if enum is None:
            return False
        return enum.is_valid(value)

    # ------------------------------------------------------------------
    # UI dropdown helpers
    # ------------------------------------------------------------------

    def active_options(self, enum_name: str) -> list[dict]:
        """Return non-deprecated options suitable for 'create' mode dropdowns."""
        enum = self.get_or_raise(enum_name)
        return [
            {"value": v.value, "label": v.display}
            for v in enum.active_values
        ]

    def all_options(self, enum_name: str) -> list[dict]:
        """Return all options (including deprecated) for 'edit'/'filter' mode."""
        enum = self.get_or_raise(enum_name)
        return [
            {
                "value": v.value,
                "label": v.display,
                "deprecated": v.deprecated,
            }
            for v in enum.values
        ]

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name
