"""
UI formatter — JSON-serialisable dicts for API responses consumed by the frontend.
"""
from __future__ import annotations

from backend.skill_registry.models.enum import EnumSkill
from backend.skill_registry.models.mutation import Mutation, MutationFile
from backend.skill_registry.models.skill import EntitySkill, FieldDef


# ---------------------------------------------------------------------------
# Enum formatting
# ---------------------------------------------------------------------------


def format_enum_options(enum: EnumSkill, mode: str = "create") -> dict:
    """
    Return UI-ready dropdown options.

    mode:
      'create' -> active (non-deprecated) options only
      'edit'   -> all options including deprecated
      'filter' -> all options including deprecated
    """
    if mode == "create":
        values = enum.active_values
    else:
        values = enum.values

    return {
        "name": enum.name,
        "description": enum.description,
        "options": [
            {
                "value": v.value,
                "label": v.display,
                "deprecated": v.deprecated,
            }
            for v in values
        ],
    }


def format_enum_full(enum: EnumSkill) -> dict:
    """Full enum definition including all metadata."""
    return {
        "name": enum.name,
        "description": enum.description,
        "group": enum.group,
        "values": [
            {
                "value": v.value,
                "display": v.display,
                "description": v.description,
                "deprecated": v.deprecated,
            }
            for v in enum.values
        ],
    }


# ---------------------------------------------------------------------------
# Entity / field formatting
# ---------------------------------------------------------------------------


def format_field_meta(field: FieldDef) -> dict:
    """Serialise a FieldDef for the /schema/{entity}/fields endpoint."""
    return {
        "name": field.name,
        "label": field.display_label,
        "type": field.type,
        "isPK": field.isPK,
        "nullable": field.nullable,
        "sensitive": field.sensitive,
        "enumRef": field.enumRef,
        "fk": (
            {
                "entity": field.fk.entity,
                "field": field.fk.field,
                "displayField": field.fk.display_field,
            }
            if field.fk
            else None
        ),
        "maxLength": field.max_length,
        "readOnly": field.read_only,
        "description": field.description,
    }


def format_display_config(skill: EntitySkill) -> dict:
    """Serialise the DisplayConfig for the /schema/{entity}/display endpoint."""
    dc = skill.display
    return {
        "entity": skill.entity,
        "defaultSortField": dc.default_sort_field,
        "defaultSortDir": dc.default_sort_dir,
        "columnsVisible": dc.columns_visible,
        "detailFields": dc.detail_fields,
        "searchableFields": dc.searchable_fields,
        "pageSize": dc.page_size,
    }


# ---------------------------------------------------------------------------
# Mutation formatting
# ---------------------------------------------------------------------------


def format_mutation_def(mutation: Mutation) -> dict:
    """Serialise a Mutation for the /schema/{entity}/mutations endpoint."""
    return {
        "id": mutation.id,
        "operation": mutation.operation,
        "description": mutation.description,
        "fields": mutation.fields,
        "requiresConfirmation": mutation.requires_confirmation,
        "validationRules": [
            {
                "field": rule.field,
                "rule": rule.rule,
                "value": rule.value,
                "message": rule.message,
            }
            for rule in mutation.validation_rules
        ],
    }


def format_mutation_defs(mf: MutationFile) -> list[dict]:
    return [format_mutation_def(m) for m in mf.mutations]
