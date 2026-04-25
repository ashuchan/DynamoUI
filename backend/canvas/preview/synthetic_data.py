"""PreviewBuilder — turns a partial CanvasIntent + skill YAML into PreviewData
for the FE's <ScopedThemeProvider> + <ArchetypePreview> components.

Determinism: rows are seeded from hash(session_id + serialised intent) so
repeated polls with the same intent return byte-identical rows. This stops
the preview from "shimmering" while the FE polls every 2s.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.canvas.api.schemas import PreviewData, PreviewField
from backend.canvas.models.intent import (
    CanvasIntent,
    LayoutArchetype,
    NavStyle,
)
from backend.canvas.synthesis.layout_synthesiser import LayoutSynthesiser
from backend.canvas.synthesis.skill_enricher import SkillEnricher
from backend.canvas.synthesis.theme_synthesiser import ThemeSynthesiser

PREVIEW_ROW_COUNT = 12


class PreviewBuilder:
    """Stateless builder; main injects skill/enum lookups via constructor."""

    def __init__(
        self,
        theme: ThemeSynthesiser,
        layout: LayoutSynthesiser,
        enricher: SkillEnricher,
    ) -> None:
        self._theme = theme
        self._layout = layout
        self._enricher = enricher

    def build(
        self,
        session_id: str,
        intent: CanvasIntent,
        skill_yaml: dict[str, Any] | None,
        enum_values: dict[str, list[str]] | None = None,
    ) -> PreviewData:
        # Default skill scaffold if FE has not supplied one yet.
        if skill_yaml is None:
            skill_yaml = self._fallback_skill(intent)

        enriched = self._enricher.enrich(skill_yaml, intent)
        fields = self._build_fields(enriched, intent)
        rng = self._seed(session_id, intent)
        rows = [
            self._fake_row(fields, intent, enum_values or {}, rng)
            for _ in range(PREVIEW_ROW_COUNT)
        ]

        # Theme + layout — falls back to enterprise/dashboard while the
        # operator is still answering questions.
        theme_css = self._safe_theme_css(intent)
        try:
            layout_cfg = self._layout.synthesise(intent)
            archetype = layout_cfg.archetype
            nav_style = layout_cfg.nav_style
            metric_fields = layout_cfg.metric_card_fields
        except Exception:
            archetype = LayoutArchetype.DASHBOARD
            nav_style = NavStyle.SIDEBAR
            metric_fields = list(intent.key_monetary_fields[:4]) if intent.key_monetary_fields else []

        return PreviewData(
            entity=intent.primary_entity or enriched.get("entity", "Item"),
            fields=fields,
            rows=rows,
            archetype=archetype,
            theme_css=theme_css,
            nav_style="sidebar" if nav_style == NavStyle.SIDEBAR else "top_nav",
            metric_fields=metric_fields,
        )

    @staticmethod
    def cache_key(session_id: str, intent: CanvasIntent) -> str:
        payload = json.dumps(intent.model_dump(), sort_keys=True, default=str)
        return hashlib.sha256(f"{session_id}|{payload}".encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _seed(session_id: str, intent: CanvasIntent) -> random.Random:
        digest = PreviewBuilder.cache_key(session_id, intent)
        return random.Random(int(digest[:8], 16))

    def _safe_theme_css(self, intent: CanvasIntent) -> str:
        try:
            return self._theme.synthesise(intent)
        except Exception:
            # Use enterprise + standard until both are picked.
            from backend.canvas.models.intent import (
                AestheticMood,
                CanvasIntent as Ci,
                DensityPreference,
            )
            fallback = Ci(
                session_id=intent.session_id,
                aesthetic_mood=AestheticMood.ENTERPRISE,
                density=DensityPreference.STANDARD,
            )
            return self._theme.synthesise(fallback)

    @staticmethod
    def _fallback_skill(intent: CanvasIntent) -> dict[str, Any]:
        entity = intent.primary_entity or "Item"
        return {
            "entity": entity,
            "label_singular": entity,
            "label_plural": f"{entity}s",
            "fields": [
                {"name": "id", "type": "uuid", "isPK": True},
                {"name": "name", "type": "string"},
                {"name": "status", "type": "string", "enumRef": "status_enum"},
                {"name": "amount", "type": "decimal"},
                {"name": "created_at", "type": "timestamp"},
            ],
        }

    @staticmethod
    def _build_fields(
        enriched: dict[str, Any], intent: CanvasIntent
    ) -> list[PreviewField]:
        out: list[PreviewField] = []
        for f in enriched.get("fields", []) or []:
            name = f.get("name", "")
            label = f.get("label", name.replace("_", " ").title())
            hint = f.get("display_hint", "text")
            priority = f.get("column_priority", "medium")
            is_status = (
                name in (intent.key_status_fields or [])
                or bool(f.get("enumRef"))
                or hint == "badge"
            )
            is_monetary = (
                name in (intent.key_monetary_fields or []) or hint == "currency"
            )
            out.append(
                PreviewField(
                    name=name,
                    label=label,
                    display_hint=hint,
                    column_priority=priority if priority in ("high", "medium", "low") else "medium",
                    is_status=is_status,
                    is_monetary=is_monetary,
                )
            )
        return out

    @staticmethod
    def _fake_row(
        fields: list[PreviewField],
        intent: CanvasIntent,
        enum_values: dict[str, list[str]],
        rng: random.Random,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {}
        statuses = ["Pending", "In Progress", "Completed", "Cancelled"]
        for f in fields:
            if f.is_status:
                # If a real enum is available use it, else fall back to a
                # canonical Kanban-friendly set so the FE's grouping still works.
                values = enum_values.get(f.name) or statuses
                row[f.name] = rng.choice(values)
            elif f.is_monetary or f.display_hint == "currency":
                row[f.name] = round(rng.uniform(50, 9999), 2)
            elif f.display_hint in ("relative_time", "datetime"):
                delta = timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23))
                row[f.name] = (datetime.now(timezone.utc) - delta).isoformat()
            elif f.display_hint == "toggle":
                row[f.name] = rng.choice([True, False])
            elif f.name in ("id", "uuid"):
                row[f.name] = f"{intent.primary_entity or 'item'}-{rng.randint(1000, 9999)}"
            else:
                row[f.name] = f"{f.label} {rng.randint(1, 99)}"
        return row
