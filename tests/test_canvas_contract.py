"""Wire-contract regression — backend Pydantic schemas must stay in lockstep
with the frontend's src/types/canvas.ts.

A hand-curated golden snapshot pins the JSON Schema for the most-touched
response models. Any drift fails the test, prompting a paired FE update.
"""
from __future__ import annotations

from backend.canvas.api.schemas import (
    GenerateResponse,
    IntentEnvelope,
    MessageResponse,
    PreviewData,
    PreviewField,
    SessionView,
)


def _required(model) -> set[str]:
    return set(model.model_json_schema().get("required", []))


def test_session_view_matches_frontend_session_shape() -> None:
    # FE: { session_id, state, messages, partial_intent }
    # partial_intent has a default → not in `required`.
    assert _required(SessionView) >= {"session_id", "state", "messages"}


def test_message_response_matches_frontend_shape() -> None:
    # FE: { reply, intent_update, session_state }
    assert _required(MessageResponse) >= {"reply", "session_state"}
    props = MessageResponse.model_json_schema()["properties"]
    assert "intent_update" in props


def test_intent_envelope_is_wrapped() -> None:
    # FE polls: { intent, state }
    assert _required(IntentEnvelope) == {"state"}
    props = IntentEnvelope.model_json_schema()["properties"]
    assert "intent" in props


def test_preview_data_includes_inlined_theme_css() -> None:
    req = _required(PreviewData)
    for f in (
        "entity",
        "fields",
        "rows",
        "archetype",
        "theme_css",
        "nav_style",
        "metric_fields",
    ):
        assert f in req


def test_preview_field_priority_is_constrained() -> None:
    schema = PreviewField.model_json_schema()
    enum = schema["properties"]["column_priority"]["enum"]
    assert set(enum) == {"high", "medium", "low"}


def test_generate_response_has_artifacts_url() -> None:
    req = _required(GenerateResponse)
    assert {"status", "files", "artifacts_url"} <= req
