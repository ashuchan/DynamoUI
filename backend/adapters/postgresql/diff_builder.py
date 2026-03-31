"""
DiffBuilder — generates a human-readable diff preview for mutations.
In-memory only — no database writes occur here.
"""
from __future__ import annotations

from typing import Any

import structlog

from backend.adapters.base import MutationPlan

log = structlog.get_logger(__name__)


class DiffBuilder:
    """
    Generates plain-text diff previews for mutation confirmation dialogs.
    Phase 1 uses a plain-text table diff. Phase 4 will upgrade to visual modal.
    """

    def build_create_preview(
        self,
        plan: MutationPlan,
        proposed_fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview for a CREATE operation."""
        rows = [
            {"field": k, "before": None, "after": str(v)}
            for k, v in proposed_fields.items()
        ]
        result = {
            "operation": "create",
            "entity": plan.entity,
            "mutation_id": plan.mutation_id,
            "diff": rows,
            "summary": f"Create new {plan.entity} with {len(rows)} field(s)",
        }
        log.debug("diff_builder.create", entity=plan.entity, fields=len(rows))
        return result

    def build_update_preview(
        self,
        plan: MutationPlan,
        existing_record: dict[str, Any],
        proposed_fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview for an UPDATE operation — shows before/after for changed fields only."""
        rows = []
        for field, new_val in proposed_fields.items():
            old_val = existing_record.get(field)
            if old_val != new_val:
                rows.append({
                    "field": field,
                    "before": str(old_val) if old_val is not None else None,
                    "after": str(new_val),
                })
        result = {
            "operation": "update",
            "entity": plan.entity,
            "mutation_id": plan.mutation_id,
            "record_pk": plan.record_pk,
            "diff": rows,
            "summary": f"Update {plan.entity} #{plan.record_pk} — {len(rows)} field(s) changed",
        }
        log.debug(
            "diff_builder.update",
            entity=plan.entity,
            pk=plan.record_pk,
            changes=len(rows),
        )
        return result

    def build_delete_preview(
        self,
        plan: MutationPlan,
        existing_record: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview for a DELETE operation — shows the record that will be deleted."""
        rows = [
            {"field": k, "before": str(v), "after": None}
            for k, v in existing_record.items()
        ]
        result = {
            "operation": "delete",
            "entity": plan.entity,
            "mutation_id": plan.mutation_id,
            "record_pk": plan.record_pk,
            "diff": rows,
            "summary": f"DELETE {plan.entity} #{plan.record_pk} — this cannot be undone",
        }
        log.debug(
            "diff_builder.delete",
            entity=plan.entity,
            pk=plan.record_pk,
        )
        return result
