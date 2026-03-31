"""
Skill field types <-> SQLAlchemy column types.
"""
from __future__ import annotations

import sqlalchemy as sa

# Skill type -> SQLAlchemy type constructor
# Text used when no max_length is specified for strings.
# BigInteger for integer PKs.
TYPE_MAP: dict[str, sa.types.TypeEngine] = {
    "string": sa.Text(),
    "integer": sa.Integer(),
    "float": sa.Numeric(),
    "boolean": sa.Boolean(),
    "date": sa.DateTime(),
    "uuid": sa.UUID(as_uuid=False),
    "enum": sa.String(255),     # Stored as string, validated via EnumRegistry
    "json": sa.JSON(),
}


def get_column_type(
    field_type: str,
    *,
    max_length: int | None = None,
    is_pk: bool = False,
) -> sa.types.TypeEngine:
    """
    Return the appropriate SQLAlchemy type for a skill field.

    Rules:
    - string with max_length -> sa.String(max_length)
    - string without max_length -> sa.Text()
    - integer PK -> sa.BigInteger()
    - float/currency -> sa.Numeric()
    - uuid -> sa.UUID (native PostgreSQL UUID)
    """
    if field_type == "string":
        if max_length is not None:
            return sa.String(max_length)
        return sa.Text()

    if field_type == "integer":
        if is_pk:
            return sa.BigInteger()
        return sa.Integer()

    if field_type == "float":
        return sa.Numeric()

    return TYPE_MAP.get(field_type, sa.Text())
