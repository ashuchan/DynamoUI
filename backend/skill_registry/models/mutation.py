"""
Pydantic v2 models for *.mutations.yaml files.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ValidationRule(BaseModel):
    """A server-side validation rule applied before executing a mutation."""

    field: str = Field(..., description="Field name this rule applies to")
    rule: Literal["required", "min", "max", "regex", "enum_ref"] = "required"
    value: str = Field("", description="Rule parameter, e.g. regex pattern or min value")
    message: str = Field("", description="User-facing error message on failure")


class Mutation(BaseModel):
    """A single mutation definition (create / update / delete)."""

    id: str = Field(..., description="Unique identifier, e.g. employee.create")
    operation: Literal["create", "update", "delete"]
    description: str = Field("")
    fields: list[str] = Field(
        default_factory=list,
        description="Field names included in this mutation; empty = all writable fields",
    )
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    requires_confirmation: bool = Field(
        True,
        description="Always true in Phase 1 — diff preview gate must not be bypassed",
    )
    notification_on_success: bool = Field(False)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$", v):
            raise ValueError(
                f"Mutation.id must be dot.separated.snake_case, got: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _delete_has_no_fields(self) -> Mutation:
        if self.operation == "delete" and self.fields:
            raise ValueError(
                f"Mutation {self.id!r} is a delete operation but lists fields — "
                "delete operations act on the whole record"
            )
        return self


class MutationFile(BaseModel):
    """Represents a parsed *.mutations.yaml file."""

    entity: str = Field(..., description="PascalCase entity this file belongs to")
    mutations: list[Mutation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_mutation_ids(self) -> MutationFile:
        seen: set[str] = set()
        for m in self.mutations:
            if m.id in seen:
                raise ValueError(
                    f"Duplicate mutation id {m.id!r} in entity {self.entity!r}"
                )
            seen.add(m.id)
        return self
