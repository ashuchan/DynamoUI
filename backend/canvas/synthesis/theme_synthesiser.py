"""ThemeSynthesiser — CanvasIntent → CSS string with --dui-* tokens (LLD 9 §7).

The 6 mood presets are the canonical truth. Density tokens are additive — they
never overwrite a token defined by the preset, only add new --dui-row-height,
--dui-cell-padding-y, etc.
"""
from __future__ import annotations

from backend.canvas.models.intent import (
    AestheticMood,
    CanvasIntent,
    DensityPreference,
    ThemeManifest,
)


class CanvasValidationError(Exception):
    """Raised when a synthesised artifact fails its existing CI validator."""


THEME_PRESETS: dict[AestheticMood, ThemeManifest] = {
    AestheticMood.ENTERPRISE: ThemeManifest(
        name="slate-pro",
        primary="#1e40af",        primary_hover="#1d3f9e",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f8fafc",
        surface_tertiary="#f1f5f9",
        text="#0f172a",           text_secondary="#475569",
        text_muted="#94a3b8",
        border="#e2e8f0",         border_strong="#cbd5e1",
        success="#15803d",        warning="#b45309",
        error="#b91c1c",          info="#1e40af",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.125rem",     radius_md="0.25rem",  radius_lg="0.375rem",
        primary_dark="#3b82f6",   surface_dark="#0f172a",
        surface_secondary_dark="#1e293b", surface_tertiary_dark="#334155",
        text_dark="#f1f5f9",      text_secondary_dark="#94a3b8",
        border_dark="#334155",    border_strong_dark="#475569",
    ),
    AestheticMood.MODERN_SAAS: ThemeManifest(
        name="indigo-saas",
        primary="#4f46e5",        primary_hover="#4338ca",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f9fafb",
        surface_tertiary="#f3f4f6",
        text="#111827",           text_secondary="#6b7280",
        text_muted="#9ca3af",
        border="#e5e7eb",         border_strong="#d1d5db",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#4f46e5",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.25rem",      radius_md="0.375rem", radius_lg="0.5rem",
        primary_dark="#6366f1",   surface_dark="#111827",
        surface_secondary_dark="#1f2937", surface_tertiary_dark="#374151",
        text_dark="#f9fafb",      text_secondary_dark="#9ca3af",
        border_dark="#374151",    border_strong_dark="#4b5563",
    ),
    AestheticMood.FUNCTIONAL: ThemeManifest(
        name="zinc-ops",
        primary="#3f3f46",        primary_hover="#27272a",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#fafafa",
        surface_tertiary="#f4f4f5",
        text="#18181b",           text_secondary="#52525b",
        text_muted="#a1a1aa",
        border="#e4e4e7",         border_strong="#d4d4d8",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#3f3f46",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.125rem",     radius_md="0.25rem",  radius_lg="0.25rem",
        primary_dark="#71717a",   surface_dark="#18181b",
        surface_secondary_dark="#27272a", surface_tertiary_dark="#3f3f46",
        text_dark="#fafafa",      text_secondary_dark="#a1a1aa",
        border_dark="#3f3f46",    border_strong_dark="#52525b",
    ),
    AestheticMood.FRIENDLY: ThemeManifest(
        name="violet-soft",
        primary="#7c3aed",        primary_hover="#6d28d9",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#faf5ff",
        surface_tertiary="#f3e8ff",
        text="#1c1917",           text_secondary="#57534e",
        text_muted="#a8a29e",
        border="#e9d5ff",         border_strong="#d8b4fe",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#7c3aed",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.375rem",     radius_md="0.5rem",   radius_lg="0.75rem",
        primary_dark="#a78bfa",   surface_dark="#1c1917",
        surface_secondary_dark="#292524", surface_tertiary_dark="#44403c",
        text_dark="#fafaf9",      text_secondary_dark="#a8a29e",
        border_dark="#44403c",    border_strong_dark="#57534e",
    ),
    AestheticMood.CLINICAL: ThemeManifest(
        name="teal-clinical",
        primary="#0f766e",        primary_hover="#0d6663",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#f0fdfa",
        surface_tertiary="#ccfbf1",
        text="#134e4a",           text_secondary="#0f766e",
        text_muted="#5eead4",
        border="#99f6e4",         border_strong="#5eead4",
        success="#15803d",        warning="#b45309",
        error="#b91c1c",          info="#0f766e",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.25rem",      radius_md="0.375rem", radius_lg="0.5rem",
        primary_dark="#14b8a6",   surface_dark="#042f2e",
        surface_secondary_dark="#0d3330", surface_tertiary_dark="#134e4a",
        text_dark="#f0fdfa",      text_secondary_dark="#5eead4",
        border_dark="#134e4a",    border_strong_dark="#0f766e",
    ),
    AestheticMood.BOLD_CONSUMER: ThemeManifest(
        name="rose-consumer",
        primary="#e11d48",        primary_hover="#be123c",
        primary_foreground="#ffffff",
        surface="#ffffff",        surface_secondary="#fff1f2",
        surface_tertiary="#ffe4e6",
        text="#0f172a",           text_secondary="#64748b",
        text_muted="#94a3b8",
        border="#fecdd3",         border_strong="#fda4af",
        success="#16a34a",        warning="#d97706",
        error="#dc2626",          info="#e11d48",
        font_sans="'Inter', system-ui, sans-serif",
        font_mono="'JetBrains Mono', monospace",
        radius_sm="0.375rem",     radius_md="0.5rem",   radius_lg="1rem",
        primary_dark="#fb7185",   surface_dark="#0f0a0a",
        surface_secondary_dark="#1c0a0a", surface_tertiary_dark="#350d0d",
        text_dark="#fff1f2",      text_secondary_dark="#fda4af",
        border_dark="#350d0d",    border_strong_dark="#9f1239",
    ),
}


DENSITY_TOKENS: dict[DensityPreference, dict[str, str]] = {
    DensityPreference.COMPACT: {
        "row_height": "2rem",
        "cell_padding_y": "0.25rem",
        "card_padding": "0.75rem",
        "section_gap": "0.75rem",
    },
    DensityPreference.STANDARD: {
        "row_height": "2.75rem",
        "cell_padding_y": "0.5rem",
        "card_padding": "1rem",
        "section_gap": "1rem",
    },
    DensityPreference.COMFORTABLE: {
        "row_height": "3.5rem",
        "cell_padding_y": "0.75rem",
        "card_padding": "1.5rem",
        "section_gap": "1.5rem",
    },
}


# Required tokens enforced by scripts/validate_theme.py. We mirror the contract
# here so unit tests don't need to subprocess into the validator.
REQUIRED_TOKENS = (
    "--dui-primary", "--dui-primary-hover", "--dui-primary-foreground",
    "--dui-surface", "--dui-surface-secondary", "--dui-surface-tertiary",
    "--dui-text", "--dui-text-secondary", "--dui-text-muted",
    "--dui-border", "--dui-border-strong",
    "--dui-success", "--dui-warning", "--dui-error", "--dui-info",
    "--dui-font-sans", "--dui-font-mono",
    "--dui-radius-sm", "--dui-radius-md", "--dui-radius-lg",
)


class ThemeSynthesiser:
    """CanvasIntent → CSS string. Refuses to return invalid output."""

    def synthesise(self, intent: CanvasIntent) -> str:
        if intent.aesthetic_mood is None:
            raise CanvasValidationError("aesthetic_mood is required for theme synthesis")
        if intent.density is None:
            raise CanvasValidationError("density is required for theme synthesis")

        preset = THEME_PRESETS[intent.aesthetic_mood]
        density = DENSITY_TOKENS[intent.density]
        name = intent.custom_theme_name or preset.name

        css = self._render_css(preset, density, name)
        self._validate(css)
        return css

    def manifest_for(self, intent: CanvasIntent) -> ThemeManifest:
        if intent.aesthetic_mood is None:
            raise CanvasValidationError("aesthetic_mood is required")
        return THEME_PRESETS[intent.aesthetic_mood]

    @staticmethod
    def _render_css(m: ThemeManifest, d: dict[str, str], name: str) -> str:
        # Aliases (--dui-text-primary, --dui-bg, --dui-danger, --dui-badge-*)
        # mirror the names the frontend's Tailwind config and preview
        # components reference. They MUST stay in sync with both this file and
        # the FE preview components.
        return f"""/* DynamoUI Theme: {name} — generated by Canvas */
/* DO NOT EDIT — re-run Canvas to regenerate */
:root {{
  --dui-primary: {m.primary};
  --dui-primary-hover: {m.primary_hover};
  --dui-primary-foreground: {m.primary_foreground};
  --dui-surface: {m.surface};
  --dui-surface-secondary: {m.surface_secondary};
  --dui-surface-tertiary: {m.surface_tertiary};
  --dui-bg: {m.surface};
  --dui-text: {m.text};
  --dui-text-primary: {m.text};
  --dui-text-secondary: {m.text_secondary};
  --dui-text-muted: {m.text_muted};
  --dui-border: {m.border};
  --dui-border-strong: {m.border_strong};
  --dui-success: {m.success};
  --dui-warning: {m.warning};
  --dui-error: {m.error};
  --dui-danger: {m.error};
  --dui-info: {m.info};
  --dui-badge-bg: {m.surface_tertiary};
  --dui-badge-text: {m.text_secondary};
  --dui-font-sans: {m.font_sans};
  --dui-font-mono: {m.font_mono};
  --dui-radius-sm: {m.radius_sm};
  --dui-radius-md: {m.radius_md};
  --dui-radius-lg: {m.radius_lg};
  --dui-row-height: {d['row_height']};
  --dui-cell-padding-y: {d['cell_padding_y']};
  --dui-card-padding: {d['card_padding']};
  --dui-section-gap: {d['section_gap']};
}}

@media (prefers-color-scheme: dark) {{
  :root {{
    --dui-primary: {m.primary_dark};
    --dui-surface: {m.surface_dark};
    --dui-surface-secondary: {m.surface_secondary_dark};
    --dui-surface-tertiary: {m.surface_tertiary_dark};
    --dui-bg: {m.surface_dark};
    --dui-text: {m.text_dark};
    --dui-text-primary: {m.text_dark};
    --dui-text-secondary: {m.text_secondary_dark};
    --dui-border: {m.border_dark};
    --dui-border-strong: {m.border_strong_dark};
    --dui-badge-bg: {m.surface_tertiary_dark};
    --dui-badge-text: {m.text_secondary_dark};
  }}
}}
"""

    @staticmethod
    def _validate(css: str) -> None:
        missing = [t for t in REQUIRED_TOKENS if t not in css]
        if missing:
            raise CanvasValidationError(
                f"theme CSS missing required tokens: {missing}"
            )
        if "DO NOT EDIT" not in css:
            raise CanvasValidationError("theme CSS missing DO NOT EDIT header")
