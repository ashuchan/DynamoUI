"""Canvas core data models — CanvasIntent, ThemeManifest, LayoutConfig (LLD 9 §5).

Field names and enum values must remain 1:1 with the frontend's
``src/types/canvas.ts``. Drift here breaks the wire contract.
"""
from __future__ import annotations

from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Domain(str, Enum):
    FINTECH = "fintech"
    LOGISTICS = "logistics"
    HR = "hr"
    SAAS_B2B = "saas_b2b"
    HEALTHCARE = "healthcare"
    ECOMMERCE = "ecommerce"
    LEGAL = "legal"
    EDUCATION = "education"
    MANUFACTURING = "manufacturing"
    GENERIC = "generic"


class AestheticMood(str, Enum):
    ENTERPRISE = "enterprise"
    FUNCTIONAL = "functional"
    MODERN_SAAS = "modern_saas"
    FRIENDLY = "friendly"
    CLINICAL = "clinical"
    BOLD_CONSUMER = "bold_consumer"


class OperationProfile(str, Enum):
    READ_HEAVY = "read_heavy"
    WRITE_HEAVY = "write_heavy"
    REVIEW_AUDIT = "review_audit"
    MIXED = "mixed"


class DensityPreference(str, Enum):
    COMPACT = "compact"
    STANDARD = "standard"
    COMFORTABLE = "comfortable"


class LayoutArchetype(str, Enum):
    DASHBOARD = "dashboard"
    DATA_ENTRY = "data_entry"
    REVIEW_AUDIT = "review_audit"
    KANBAN = "kanban"
    TIMELINE = "timeline"


class NavStyle(str, Enum):
    SIDEBAR = "sidebar"
    TOP_NAV = "top_nav"


class CanvasIntent(BaseModel):
    """Structured output of the Canvas conversation.

    All fields except ``session_id`` are optional during elicitation; the
    conversation driver fills them in incrementally. Once every required
    field is populated, the session transitions to CONFIRMING.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    domain: Domain | None = None
    aesthetic_mood: AestheticMood | None = None
    operation_profile: OperationProfile | None = None
    density: DensityPreference | None = None
    primary_entity: str | None = None
    entity_priorities: list[str] = Field(default_factory=list)
    key_status_fields: list[str] = Field(default_factory=list)
    key_monetary_fields: list[str] = Field(default_factory=list)
    key_datetime_fields: list[str] = Field(default_factory=list)
    enable_kanban: bool = False
    enable_timeline: bool = False
    custom_theme_name: str | None = None
    operator_notes: str = ""

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "domain",
        "aesthetic_mood",
        "operation_profile",
        "density",
        "primary_entity",
    )

    def is_complete(self) -> bool:
        return all(getattr(self, f) is not None for f in self.REQUIRED_FIELDS)


class ThemeManifest(BaseModel):
    """Internal theme representation. Rendered to CSS by ThemeSynthesiser."""

    name: str
    primary: str
    primary_hover: str
    primary_foreground: str
    surface: str
    surface_secondary: str
    surface_tertiary: str
    text: str
    text_secondary: str
    text_muted: str
    border: str
    border_strong: str
    success: str
    warning: str
    error: str
    info: str
    font_sans: str
    font_mono: str
    radius_sm: str
    radius_md: str
    radius_lg: str
    primary_dark: str
    surface_dark: str
    surface_secondary_dark: str
    surface_tertiary_dark: str
    text_dark: str
    text_secondary_dark: str
    border_dark: str
    border_strong_dark: str


class LayoutConfig(BaseModel):
    archetype: LayoutArchetype
    nav_style: NavStyle
    primary_entity: str
    sidebar_entities: list[str] = Field(default_factory=list)
    metric_card_fields: list[str] = Field(default_factory=list)
    bulk_action_enabled: bool = False
    export_enabled: bool = False
    density: DensityPreference
