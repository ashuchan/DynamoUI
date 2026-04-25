"""SkillEnricher — fills in display_hint, widget_type, column_priority,
badge_style on skill YAML field dicts (LLD 9 §9).

Rules:
- Existing operator-authored display_hint values are NEVER overwritten.
- PK fields always end up at column_priority=low (post-pass override).
- Otherwise the type/role inference table applies.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.canvas.models.intent import CanvasIntent


PK_FIELD_NAMES = {"id", "uuid"}
TIMESTAMP_FIELD_NAMES = {"created_at", "updated_at"}


class SkillEnricher:
    def enrich(self, skill_yaml: dict[str, Any], intent: CanvasIntent) -> dict[str, Any]:
        out = deepcopy(skill_yaml)
        for field in out.get("fields", []) or []:
            self._enrich_field(field, intent)
        return out

    @staticmethod
    def _enrich_field(field: dict[str, Any], intent: CanvasIntent) -> None:
        existing_hint = field.get("display_hint")
        fname = field.get("name", "")
        ftype = field.get("type", "string")

        # Type/role inference table (only when no existing hint).
        if not existing_hint:
            if fname in (intent.key_status_fields or []) or field.get("enumRef"):
                field["display_hint"] = "badge"
                field["badge_style"] = field.get("badge_style", "semantic")
                field["widget_type"] = field.get("widget_type", "select")
                field["column_priority"] = field.get("column_priority", "high")
            elif fname in (intent.key_monetary_fields or []):
                field["display_hint"] = "currency"
                field["widget_type"] = field.get("widget_type", "number")
                field["column_priority"] = field.get("column_priority", "high")
            elif fname in (intent.key_datetime_fields or []) or ftype in ("timestamp", "date"):
                field["display_hint"] = "relative_time"
                field["widget_type"] = field.get("widget_type", "datepicker")
                field["column_priority"] = field.get("column_priority", "medium")
            elif ftype == "boolean":
                field["display_hint"] = "toggle"
                field["widget_type"] = field.get("widget_type", "checkbox")
                field["column_priority"] = field.get("column_priority", "low")
            elif field.get("isFK"):
                field["display_hint"] = "link"
                field["widget_type"] = field.get("widget_type", "fk_select")
                field["column_priority"] = field.get("column_priority", "medium")
            else:
                field["display_hint"] = "text"
                field["widget_type"] = field.get("widget_type", "input")
                field["column_priority"] = field.get("column_priority", "medium")

        # Post-pass overrides — apply regardless of existing hint.
        # PK or low-importance technical columns are always demoted to "low".
        if field.get("isPK") or fname in PK_FIELD_NAMES or fname in TIMESTAMP_FIELD_NAMES:
            field["column_priority"] = "low"
