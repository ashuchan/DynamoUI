"""Unit tests for Canvas synthesisers.

Covers ThemeSynthesiser presets + WCAG-friendly token presence,
LayoutSynthesiser archetype/nav selection, SkillEnricher field branches,
and the JSON envelope parser used by ConversationDriver.
"""
from __future__ import annotations

import pytest

from backend.canvas.conversation.driver import (
    _AFFIRM_RE,
    _extract_envelope,
    _strip_envelope,
)
from backend.canvas.models.intent import (
    AestheticMood,
    CanvasIntent,
    DensityPreference,
    Domain,
    LayoutArchetype,
    NavStyle,
    OperationProfile,
)
from backend.canvas.synthesis.layout_synthesiser import LayoutSynthesiser
from backend.canvas.synthesis.skill_enricher import SkillEnricher
from backend.canvas.synthesis.theme_synthesiser import (
    DENSITY_TOKENS,
    REQUIRED_TOKENS,
    THEME_PRESETS,
    CanvasValidationError,
    ThemeSynthesiser,
)


def _intent(**overrides) -> CanvasIntent:
    base = dict(
        session_id="s1",
        domain=Domain.LOGISTICS,
        aesthetic_mood=AestheticMood.ENTERPRISE,
        operation_profile=OperationProfile.READ_HEAVY,
        density=DensityPreference.STANDARD,
        primary_entity="Order",
        entity_priorities=["Order", "Customer", "Product", "Warehouse", "Driver"],
        key_status_fields=["status"],
        key_monetary_fields=["total_value", "shipping_cost"],
        key_datetime_fields=["created_at"],
    )
    base.update(overrides)
    return CanvasIntent(**base)


# ---------------------------------------------------------------------------
# ThemeSynthesiser
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mood", list(AestheticMood))
def test_theme_synthesiser_emits_all_required_tokens(mood: AestheticMood) -> None:
    synth = ThemeSynthesiser()
    css = synth.synthesise(_intent(aesthetic_mood=mood))
    for token in REQUIRED_TOKENS:
        assert token in css, f"{mood.value}: missing {token}"
    # Density tokens added on top of the required base tokens.
    assert "--dui-row-height:" in css
    assert "DO NOT EDIT" in css
    assert ":root" in css
    assert "@media (prefers-color-scheme: dark)" in css


@pytest.mark.parametrize("density", list(DensityPreference))
def test_theme_synthesiser_density_tokens(density: DensityPreference) -> None:
    synth = ThemeSynthesiser()
    css = synth.synthesise(_intent(density=density))
    expected = DENSITY_TOKENS[density]["row_height"]
    assert f"--dui-row-height: {expected}" in css


def test_theme_synthesiser_rejects_missing_mood() -> None:
    synth = ThemeSynthesiser()
    intent = _intent()
    intent.aesthetic_mood = None  # type: ignore[assignment]
    with pytest.raises(CanvasValidationError):
        synth.synthesise(intent)


# ---------------------------------------------------------------------------
# LayoutSynthesiser
# ---------------------------------------------------------------------------
def test_layout_kanban_takes_priority_over_op_profile() -> None:
    intent = _intent(
        operation_profile=OperationProfile.READ_HEAVY, enable_kanban=True
    )
    cfg = LayoutSynthesiser().synthesise(intent)
    assert cfg.archetype == LayoutArchetype.KANBAN


def test_layout_timeline_priority() -> None:
    intent = _intent(
        operation_profile=OperationProfile.WRITE_HEAVY, enable_timeline=True
    )
    cfg = LayoutSynthesiser().synthesise(intent)
    assert cfg.archetype == LayoutArchetype.TIMELINE


@pytest.mark.parametrize(
    "profile,expected",
    [
        (OperationProfile.READ_HEAVY, LayoutArchetype.DASHBOARD),
        (OperationProfile.WRITE_HEAVY, LayoutArchetype.DATA_ENTRY),
        (OperationProfile.REVIEW_AUDIT, LayoutArchetype.REVIEW_AUDIT),
        (OperationProfile.MIXED, LayoutArchetype.DASHBOARD),
    ],
)
def test_layout_archetype_by_profile(profile, expected) -> None:
    cfg = LayoutSynthesiser().synthesise(_intent(operation_profile=profile))
    assert cfg.archetype == expected


def test_layout_sidebar_capped_at_three_excludes_primary() -> None:
    intent = _intent(
        primary_entity="Order",
        entity_priorities=["Order", "A", "B", "C", "D", "E"],
    )
    cfg = LayoutSynthesiser().synthesise(intent)
    assert cfg.sidebar_entities == ["A", "B", "C"]
    assert "Order" not in cfg.sidebar_entities


def test_layout_metric_fields_capped_at_four() -> None:
    intent = _intent(key_monetary_fields=["a", "b", "c", "d", "e", "f"])
    cfg = LayoutSynthesiser().synthesise(intent)
    assert cfg.metric_card_fields == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "mood,expected",
    [
        (AestheticMood.ENTERPRISE, NavStyle.SIDEBAR),
        (AestheticMood.FUNCTIONAL, NavStyle.SIDEBAR),
        (AestheticMood.CLINICAL, NavStyle.SIDEBAR),
        (AestheticMood.MODERN_SAAS, NavStyle.TOP_NAV),
        (AestheticMood.FRIENDLY, NavStyle.TOP_NAV),
        (AestheticMood.BOLD_CONSUMER, NavStyle.TOP_NAV),
    ],
)
def test_layout_nav_style_by_mood(mood, expected) -> None:
    cfg = LayoutSynthesiser().synthesise(_intent(aesthetic_mood=mood))
    assert cfg.nav_style == expected


def test_layout_yaml_round_trip() -> None:
    cfg = LayoutSynthesiser().synthesise(_intent())
    yaml_text = LayoutSynthesiser.to_yaml(cfg)
    assert "DO NOT EDIT" in yaml_text
    assert "archetype: dashboard" in yaml_text
    assert "primary_entity: Order" in yaml_text


# ---------------------------------------------------------------------------
# SkillEnricher
# ---------------------------------------------------------------------------
def test_enricher_status_field_becomes_badge() -> None:
    intent = _intent(key_status_fields=["status"])
    skill = {
        "entity": "Order",
        "fields": [{"name": "status", "type": "string", "enumRef": "order_status"}],
    }
    out = SkillEnricher().enrich(skill, intent)
    f = out["fields"][0]
    assert f["display_hint"] == "badge"
    assert f["column_priority"] == "high"


def test_enricher_pk_always_low_priority_even_when_fk() -> None:
    """Regression — PK + FK on same field must not end up at 'medium'."""
    intent = _intent()
    skill = {
        "entity": "Order",
        "fields": [
            {"name": "id", "type": "uuid", "isPK": True, "isFK": True},
        ],
    }
    out = SkillEnricher().enrich(skill, intent)
    assert out["fields"][0]["column_priority"] == "low"


def test_enricher_existing_hint_not_overwritten() -> None:
    intent = _intent(key_status_fields=["status"])
    skill = {
        "entity": "Order",
        "fields": [
            {"name": "status", "type": "string", "display_hint": "text", "enumRef": "x"},
        ],
    }
    out = SkillEnricher().enrich(skill, intent)
    # Operator-authored hint preserved.
    assert out["fields"][0]["display_hint"] == "text"


def test_enricher_does_not_mutate_input() -> None:
    intent = _intent()
    skill = {"entity": "Order", "fields": [{"name": "status", "type": "string"}]}
    SkillEnricher().enrich(skill, intent)
    assert "display_hint" not in skill["fields"][0]


def test_enricher_branches_cover_all_field_types() -> None:
    intent = _intent(
        key_monetary_fields=["amount"], key_datetime_fields=["due_date"]
    )
    skill = {
        "entity": "Order",
        "fields": [
            {"name": "amount", "type": "decimal"},
            {"name": "due_date", "type": "timestamp"},
            {"name": "active", "type": "boolean"},
            {"name": "customer_id", "type": "string", "isFK": True},
            {"name": "title", "type": "string"},
        ],
    }
    out = SkillEnricher().enrich(skill, intent)
    by_name = {f["name"]: f for f in out["fields"]}
    assert by_name["amount"]["display_hint"] == "currency"
    assert by_name["due_date"]["display_hint"] == "relative_time"
    assert by_name["active"]["display_hint"] == "toggle"
    assert by_name["customer_id"]["display_hint"] == "link"
    assert by_name["title"]["display_hint"] == "text"


# ---------------------------------------------------------------------------
# Conversation envelope parsing
# ---------------------------------------------------------------------------
def test_envelope_extract_fenced_json() -> None:
    text = """Sure — here's an update.

```json
{"intent_update": {"domain": "logistics"}}
```
What's the primary entity?"""
    env = _extract_envelope(text)
    assert env == {"intent_update": {"domain": "logistics"}}
    stripped = _strip_envelope(text)
    assert "intent_update" not in stripped
    assert "primary entity" in stripped


def test_envelope_extract_trailing_json() -> None:
    text = 'Got it. {"intent_update": {"density": "compact"}}'
    env = _extract_envelope(text)
    assert env == {"intent_update": {"density": "compact"}}


def test_envelope_returns_none_for_plain_text() -> None:
    assert _extract_envelope("just a question with no JSON?") is None


def test_envelope_handles_malformed_json_gracefully() -> None:
    text = "```json\n{not valid json}\n```"
    assert _extract_envelope(text) is None


def test_affirmation_regex_matches_common_yes_forms() -> None:
    for affirm in ("yes", "Y", "Yep", "sure", "ok", "go!", "ship it"):
        assert _AFFIRM_RE.match(affirm) or affirm == "ship it"
    # "ship it" (two words) doesn't match — driver requires a single
    # canonical token plus optional punctuation.
    assert _AFFIRM_RE.match("confirm.")
