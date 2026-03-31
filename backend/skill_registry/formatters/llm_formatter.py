"""
LLM formatter — plain text representations for LLM prompt injection.
Intentionally NOT JSON: lower token count, more natural language processing.
Sensitive fields are EXCLUDED from LLM context.
"""
from __future__ import annotations

import structlog

from backend.skill_registry.models.enum import EnumSkill
from backend.skill_registry.models.skill import EntitySkill

log = structlog.get_logger(__name__)


def format_enum_for_llm(enum: EnumSkill) -> str:
    """
    Return a plain-text representation of an enum for injection into LLM prompts.

    Example output:
        Enum: EmploymentType — Classification of employment relationship
        Valid values:
          FULL_TIME (displayed as 'Full Time') — Permanent full-time employee
          CONTRACT (displayed as 'Contractor') — Fixed-term contract worker
          INTERN (displayed as 'Intern') [DEPRECATED — do not suggest]
    """
    lines: list[str] = []
    header = f"Enum: {enum.name}"
    if enum.description:
        header += f" — {enum.description}"
    lines.append(header)
    lines.append("Valid values:")

    for v in enum.values:
        entry = f"  {v.value} (displayed as '{v.display}')"
        if v.description:
            entry += f" — {v.description}"
        if v.deprecated:
            entry += " [DEPRECATED — do not suggest]"
        lines.append(entry)

    result = "\n".join(lines)
    log.debug("llm_formatter.enum_formatted", name=enum.name, lines=len(lines))
    return result


def format_entity_for_llm(skill: EntitySkill) -> str:
    """
    Return a plain-text description of an entity for LLM prompt injection.
    Sensitive fields are replaced with a placeholder — never injected into LLM context.
    """
    lines: list[str] = []
    header = f"Entity: {skill.entity} (table: {skill.table})"
    if skill.description:
        header += f" — {skill.description}"
    lines.append(header)
    lines.append("Fields:")

    for fd in skill.fields:
        if fd.sensitive:
            # Sensitive fields must never be described to the LLM
            lines.append(f"  {fd.name}: [SENSITIVE — excluded from context]")
            continue

        field_line = f"  {fd.name} ({fd.type})"
        if fd.isPK:
            field_line += " [PK]"
        if fd.enumRef:
            field_line += f" [enum: {fd.enumRef}]"
        if fd.fk is not None:
            field_line += f" [FK -> {fd.fk.entity}.{fd.fk.field}]"
        if fd.description:
            field_line += f" — {fd.description}"
        lines.append(field_line)

    result = "\n".join(lines)
    log.debug(
        "llm_formatter.entity_formatted",
        entity=skill.entity,
        fields=len(skill.fields),
        sensitive_fields=len(skill.sensitive_fields),
    )
    return result
