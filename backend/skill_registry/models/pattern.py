"""
Pydantic v2 models for *.patterns.yaml files (skill_registry side).
The pattern_cache module has its own richer models — these are the skill registry models.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class PatternParam(BaseModel):
    """A named placeholder inside a pattern's query_template."""

    name: str = Field(..., description="Placeholder name, e.g. status")
    type: Literal["string", "integer", "float", "boolean", "date", "enum"] = "string"
    required: bool = Field(True)
    default: str | None = Field(None)
    enumRef: str = Field("", alias="enumRef")

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def _name_snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(f"PatternParam.name must be snake_case, got: {v!r}")
        return v


class Pattern(BaseModel):
    """A single query pattern within a *.patterns.yaml file."""

    id: str = Field(..., description="Globally unique pattern ID, e.g. employee.active")
    description: str = Field("")
    triggers: list[str] = Field(..., min_length=1, description="NL trigger phrases")
    query_template: str = Field(..., description="Parameterised query JSON string")
    params: list[PatternParam] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        # e.g. employee.active_by_department  (dot-separated, snake_case segments)
        if not re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", v):
            raise ValueError(
                f"Pattern.id must be dot.separated.snake_case, got: {v!r}"
            )
        return v

    @field_validator("triggers")
    @classmethod
    def _triggers_non_empty_strings(cls, v: list[str]) -> list[str]:
        for t in v:
            if not t.strip():
                raise ValueError("Pattern trigger must not be empty")
        return v


class PatternFile(BaseModel):
    """Represents a parsed *.patterns.yaml file."""

    skill_hash: str = Field(
        ..., description="16-char SHA-256 prefix of the linked skill YAML"
    )
    entity: str = Field(..., description="PascalCase entity this file belongs to")
    patterns: list[Pattern] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_pattern_ids(self) -> PatternFile:
        seen: set[str] = set()
        for p in self.patterns:
            if p.id in seen:
                raise ValueError(f"Duplicate pattern id {p.id!r} in entity {self.entity!r}")
            seen.add(p.id)
        return self
