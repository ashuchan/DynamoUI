"""
Pydantic v2 models for *.enum.yaml files.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class EnumValue(BaseModel):
    """A single value within an enum."""

    value: str = Field(..., description="Raw enum key, e.g. FULL_TIME")
    display: str = Field(..., description="Human-readable label, e.g. 'Full Time'")
    description: str = Field("", description="Optional explanation for LLM context")
    deprecated: bool = Field(False, description="If true, excluded from 'create' mode dropdowns")

    @field_validator("value")
    @classmethod
    def _value_must_be_uppercase(cls, v: str) -> str:
        if not re.match(r"^[A-Z][A-Z0-9_]*$", v):
            raise ValueError(
                f"EnumValue.value must be UPPER_SNAKE_CASE, got: {v!r}"
            )
        return v


class EnumSkill(BaseModel):
    """Represents a single *.enum.yaml file."""

    name: str = Field(..., description="PascalCase enum identifier, e.g. EmploymentType")
    description: str = Field("", description="Used in LLM context and documentation")
    group: str = Field("", description="Logical grouping for multi-enum navigation")
    values: list[EnumValue] = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def _name_must_be_pascal_case(cls, v: str) -> str:
        if not re.match(r"^[A-Z][a-zA-Z0-9]*$", v):
            raise ValueError(
                f"EnumSkill.name must be PascalCase, got: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_unique_values(self) -> EnumSkill:
        seen: set[str] = set()
        for ev in self.values:
            if ev.value in seen:
                raise ValueError(
                    f"Duplicate value {ev.value!r} in enum {self.name!r}"
                )
            seen.add(ev.value)
        return self

    @property
    def active_values(self) -> list[EnumValue]:
        """Values that are NOT deprecated — used in 'create' mode dropdowns."""
        return [v for v in self.values if not v.deprecated]

    def get_value(self, raw: str) -> EnumValue | None:
        """Return the EnumValue for a raw key, or None if not found."""
        for v in self.values:
            if v.value == raw:
                return v
        return None

    def is_valid(self, raw: str) -> bool:
        """True if *raw* exists in this enum (including deprecated)."""
        return any(v.value == raw for v in self.values)
