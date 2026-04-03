"""
LLM provider abstraction — Anthropic and Google backends.
Swap provider via DYNAMO_LLM_PROVIDER env var only.
Missing API key → warning + _NullProvider (degraded mode, not startup failure).
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import structlog

log = structlog.get_logger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the raw text response from the model."""


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int, timeout: float):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        msg = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            ),
            timeout=self._timeout,
        )
        return msg.content[0].text.strip()


class GoogleProvider(LLMProvider):
    """Uses google-generativeai. Import is lazy so app starts without it."""

    def __init__(self, api_key: str, model: str, max_tokens: int, timeout: float):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        import google.generativeai as genai
        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(
            self._model,
            system_instruction=system_prompt,
        )
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    user_prompt,
                    generation_config={"max_output_tokens": self._max_tokens},
                ),
            ),
            timeout=self._timeout,
        )
        return response.text.strip()


class _NullProvider(LLMProvider):
    """Returned when no API key is configured. Always returns empty string."""

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        log.warning("llm_provider.null_complete_called")
        return ""


def strip_markdown_json(text: str) -> str:
    """
    Strip markdown code fences that models sometimes include despite instructions.
    Handles ```json ... ``` and ``` ... ``` wrappers.
    """
    import re
    stripped = text.strip()
    # Remove opening fence (```json or ```)
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
    # Remove closing fence
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def create_provider(settings) -> LLMProvider:
    if settings.provider == "anthropic":
        key = settings.anthropic_api_key.get_secret_value()
        if not key:
            log.warning("llm_provider.anthropic_key_missing")
            return _NullProvider()
        return AnthropicProvider(
            key, settings.anthropic_model, settings.max_tokens, settings.timeout_seconds
        )
    if settings.provider == "google":
        key = settings.google_api_key.get_secret_value()
        if not key:
            log.warning("llm_provider.google_key_missing")
            return _NullProvider()
        return GoogleProvider(
            key, settings.google_model, settings.max_tokens, settings.timeout_seconds
        )
    raise ValueError(f"Unknown LLM provider: {settings.provider!r}")
