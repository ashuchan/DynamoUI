"""ConversationDriver — orchestrates LLM turns, parses intent updates, and
maintains the ELICITING → CONFIRMING → COMPLETE state machine (LLD 9 §6).

Parsing strategy: extract any fenced ```json``` block, fall back to a
top-level JSON object regex. Anything not in JSON is treated as plain
assistant text and surfaced as ``reply``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from backend.canvas.conversation.prompts import render_system_prompt
from backend.canvas.models.intent import CanvasIntent
from backend.canvas.models.session import ConversationState
from backend.skill_registry.llm.provider import LLMProvider

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# JSON envelope parsing
# ---------------------------------------------------------------------------
_FENCED_JSON_OPEN_RE = re.compile(r"```json\s*", re.IGNORECASE)
_FENCED_JSON_CLOSE_RE = re.compile(r"```")
# Fallback: any balanced top-level object anywhere in the text.
_OBJECT_OPEN_RE = re.compile(r"\{")

_AFFIRM_RE = re.compile(
    r"^\s*(yes|y|yep|yeah|sure|ok|okay|go|ship|generate|confirm|looks good|do it)[\s.!?]*$",
    re.IGNORECASE,
)


@dataclass
class TurnResult:
    reply: str
    intent_update: dict[str, Any] | None
    intent_complete: bool
    new_state: ConversationState
    new_partial: dict[str, Any] = field(default_factory=dict)


class ConversationDriver:
    def __init__(
        self,
        llm: LLMProvider,
        max_turns: int = 20,
        conversation_temperature: float = 0.3,
    ) -> None:
        self._llm = llm
        self._max_turns = max_turns
        self._temp = conversation_temperature

    @property
    def max_turns(self) -> int:
        return self._max_turns

    async def opening_message(self, skill_yaml_context: str = "") -> str:
        """First assistant turn — produced server-side without an LLM call so
        the FE sees a non-empty conversation immediately on session creation."""
        return (
            "Hi! I'm Canvas. I'll ask a few quick questions and configure your "
            "DynamoUI deployment.\n\n"
            "First question: what kind of business or workflow is this tool for? "
            "(e.g. logistics, fintech, HR, generic)"
        )

    async def turn(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        partial_intent: dict[str, Any],
        state: ConversationState,
        session_id: str,
        skill_yaml_context: str = "",
    ) -> TurnResult:
        # Affirmation in CONFIRMING → COMPLETE without an LLM call.
        if state == ConversationState.CONFIRMING and _AFFIRM_RE.match(user_message):
            return TurnResult(
                reply="Confirmed — generating now.",
                intent_update=None,
                intent_complete=True,
                new_state=ConversationState.COMPLETE,
                new_partial=partial_intent,
            )

        system_prompt = render_system_prompt(skill_yaml_context)
        user_prompt = self._build_user_prompt(history, user_message, partial_intent)

        try:
            raw = await self._llm.complete(system_prompt, user_prompt)
            assistant_text = raw.text or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("canvas.llm_call_failed", error=str(exc))
            return TurnResult(
                reply="Sorry, I had trouble processing that. Could you repeat?",
                intent_update=None,
                intent_complete=False,
                new_state=state,
                new_partial=partial_intent,
            )

        # Empty text from a null/misconfigured provider — surface a clear
        # operator-facing error instead of silently looping on "(thinking…)".
        if not assistant_text.strip():
            log.warning("canvas.llm_empty_response")
            return TurnResult(
                reply=(
                    "Canvas can't reach its language model right now "
                    "(no API key configured, or the provider returned empty). "
                    "Ask your admin to set DYNAMO_LLM_PROVIDER and the matching API key."
                ),
                intent_update=None,
                intent_complete=False,
                new_state=state,
                new_partial=partial_intent,
            )

        envelope = _extract_envelope(assistant_text)
        intent_update = envelope.get("intent_update") if envelope else None
        intent_complete = bool(envelope.get("intent_complete")) if envelope else False
        reply = _strip_envelope(assistant_text).strip() or "(thinking…)"

        # Merge update (if any) into partial; validate via CanvasIntent.
        new_partial = dict(partial_intent)
        if isinstance(intent_update, dict):
            new_partial.update(intent_update)
            try:
                CanvasIntent.model_validate({"session_id": session_id, **new_partial})
            except Exception as exc:  # noqa: BLE001
                log.warning("canvas.invalid_intent_update", error=str(exc))
                # Roll back to previous partial; surface raw reply so the FE
                # still sees the assistant text.
                new_partial = dict(partial_intent)
                intent_update = None

        # State transitions
        try:
            candidate = CanvasIntent.model_validate(
                {"session_id": session_id, **new_partial}
            )
        except Exception:
            candidate = None

        new_state = state
        if intent_complete and candidate and candidate.is_complete():
            new_state = ConversationState.COMPLETE
        elif candidate and candidate.is_complete() and state == ConversationState.ELICITING:
            new_state = ConversationState.CONFIRMING

        return TurnResult(
            reply=reply,
            intent_update=intent_update if isinstance(intent_update, dict) else None,
            intent_complete=intent_complete,
            new_state=new_state,
            new_partial=new_partial,
        )

    @staticmethod
    def _build_user_prompt(
        history: list[dict[str, str]],
        user_message: str,
        partial_intent: dict[str, Any],
    ) -> str:
        lines = []
        if partial_intent:
            lines.append(
                "Current partial CanvasIntent:\n"
                + json.dumps(partial_intent, indent=2, default=str)
            )
        if history:
            lines.append("Conversation so far:")
            for msg in history[-10:]:  # cap context
                lines.append(f"{msg['role'].upper()}: {msg['content']}")
        lines.append(f"USER: {user_message}")
        lines.append(
            "Respond with one short question for the operator. If you can derive any "
            "CanvasIntent fields from the conversation so far, include the "
            "```json {\"intent_update\": {...}} ``` block."
        )
        return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _decode_object_at(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    """Use json.JSONDecoder.raw_decode to consume one balanced object starting
    at ``start``. Returns (parsed_or_None, end_offset_or_-1)."""
    decoder = json.JSONDecoder()
    try:
        parsed, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        return None, -1
    if not isinstance(parsed, dict):
        return None, -1
    return parsed, end


def _find_envelope_span(text: str) -> tuple[dict[str, Any] | None, int, int]:
    """Locate the first JSON object envelope in ``text``.

    Preference order:
      1. Object inside a ```json fenced block.
      2. Any balanced top-level object found anywhere in ``text``.
    Returns (parsed_or_None, span_start, span_end). Spans cover the original
    fence + JSON when present so the caller can strip them cleanly.
    """
    if not text:
        return None, -1, -1

    # 1. Fenced ```json block.
    fence = _FENCED_JSON_OPEN_RE.search(text)
    if fence:
        body_start = fence.end()
        parsed, body_end = _decode_object_at(text, body_start)
        if parsed is not None:
            close = _FENCED_JSON_CLOSE_RE.search(text, body_end)
            span_end = close.end() if close else body_end
            return parsed, fence.start(), span_end

    # 2. Any balanced object anywhere.
    for m in _OBJECT_OPEN_RE.finditer(text):
        parsed, end = _decode_object_at(text, m.start())
        if parsed is not None:
            return parsed, m.start(), end

    return None, -1, -1


def _extract_envelope(text: str) -> dict[str, Any] | None:
    parsed, _start, _end = _find_envelope_span(text)
    return parsed


def _strip_envelope(text: str) -> str:
    if not text:
        return ""
    _parsed, start, end = _find_envelope_span(text)
    if start < 0:
        return text.strip()
    return (text[:start] + text[end:]).strip()
