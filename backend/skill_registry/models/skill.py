"""
Pydantic v2 models for *.skill.yaml files.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Supporting enums / literals
# ---------------------------------------------------------------------------

FieldType = Literal[
    "string", "integer", "float", "boolean", "date", "uuid", "enum", "json"
]

SortDir = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class DisplayConfig(BaseModel):
    """Controls how an entity is rendered in the UI."""

    default_sort_field: str = Field("", description="Field name for default sort")
    default_sort_dir: SortDir = Field("asc")
    columns_visible: list[str] = Field(
        default_factory=list, description="Ordered list of visible column names"
    )
    detail_fields: list[str] = Field(
        default_factory=list, description="Fields shown in DetailCard"
    )
    searchable_fields: list[str] = Field(
        default_factory=list, description="Fields that support free-text search"
    )
    page_size: int = Field(25, ge=1, le=500)


class NotificationConfig(BaseModel):
    """Notification settings — Slack/Webhook feature-flagged off in v1."""

    email_recipients: list[str] = Field(default_factory=list)
    # Phase 2: slack_channel, webhook_url


class FKReference(BaseModel):
    """Foreign-key relationship to another entity."""

    entity: str = Field(..., description="Target entity name (must exist in registry)")
    field: str = Field(..., description="Field name on the *target* entity that is the PK")
    display_field: str = Field(
        "", description="Field on the target entity to show in the UI instead of raw PK"
    )


class FieldDef(BaseModel):
    """A single field within an EntitySkill."""

    name: str = Field(..., description="snake_case column name")
    type: FieldType
    label: str = Field("", description="UI display label; defaults to title-cased name")
    description: str = Field("", description="LLM context hint")
    isPK: bool = Field(False, alias="isPK")
    nullable: bool = Field(True)
    sensitive: bool = Field(
        False, description="Masked in logs and LLM context; never excluded from query results"
    )
    enumRef: str = Field(
        "", alias="enumRef", description="EnumSkill.name — required when type='enum'"
    )
    fk: FKReference | None = Field(None, description="FK relationship descriptor")
    max_length: int | None = Field(None, ge=1)
    read_only: bool = Field(False, description="UI disables inline editing for this field")
    db_column_name: str = Field(
        "", description="Actual DB column name when it differs from name (e.g. PascalCase)"
    )

    model_config = {"populate_by_name": True}

    @field_validator("name")
    @classmethod
    def _name_must_be_snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"FieldDef.name must be snake_case, got: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_enum_ref(self) -> FieldDef:
        if self.type == "enum" and not self.enumRef:
            raise ValueError(
                f"Field {self.name!r} has type='enum' but missing enumRef"
            )
        if self.type != "enum" and self.enumRef:
            raise ValueError(
                f"Field {self.name!r} has enumRef but type is {self.type!r}, not 'enum'"
            )
        return self

    @property
    def display_label(self) -> str:
        """Human-readable label; uses explicit label or title-cases the name."""
        return self.label or self.name.replace("_", " ").title()


class EntitySkill(BaseModel):
    """Represents a single *.skill.yaml file."""

    entity: str = Field(..., description="PascalCase entity identifier, e.g. Employee")
    table: str = Field(..., description="Actual DB table name, e.g. employees")
    adapter: str = Field(..., description="Key in adapters.registry.yaml")
    description: str = Field("", description="Used in LLM context prompts")
    fields: list[FieldDef] = Field(..., min_length=1)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    mutations_file: str = Field(
        "", description="Relative path to *.mutations.yaml; empty = read-only"
    )
    patterns_file: str = Field(
        "", description="Relative path to *.patterns.yaml"
    )
    read_permissions: list[str] = Field(
        default_factory=list,
        description="JWT role names that may read this entity; empty = allow all",
    )
    write_permissions: list[str] = Field(
        default_factory=list,
        description="JWT role names that may mutate this entity; empty = deny all",
    )
    schema_name: str = Field("public", description="PostgreSQL schema name")
    db_table_name: str = Field(
        "", description="Actual DB table name when it differs from table (e.g. PascalCase)"
    )

    @field_validator("entity")
    @classmethod
    def _entity_must_be_pascal_case(cls, v: str) -> str:
        if not re.match(r"^[A-Z][a-zA-Z0-9]*$", v):
            raise ValueError(
                f"EntitySkill.entity must be PascalCase, got: {v!r}"
            )
        return v

    @field_validator("table")
    @classmethod
    def _table_must_be_snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"EntitySkill.table must be snake_case, got: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_pk_exists(self) -> EntitySkill:
        pks = [f for f in self.fields if f.isPK]
        if not pks:
            raise ValueError(
                f"Entity {self.entity!r} has no primary key field (isPK: true)"
            )
        return self

    @model_validator(mode="after")
    def _validate_unique_field_names(self) -> EntitySkill:
        seen: set[str] = set()
        for f in self.fields:
            if f.name in seen:
                raise ValueError(
                    f"Duplicate field name {f.name!r} in entity {self.entity!r}"
                )
            seen.add(f.name)
        return self

    @property
    def pk_field(self) -> FieldDef:
        """Return the primary key field. Multiple PKs not supported in Phase 1."""
        pks = [f for f in self.fields if f.isPK]
        return pks[0]

    @property
    def sensitive_fields(self) -> list[FieldDef]:
        return [f for f in self.fields if f.sensitive]

    def get_field(self, name: str) -> FieldDef | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None
