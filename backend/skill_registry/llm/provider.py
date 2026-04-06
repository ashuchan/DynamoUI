"""
LLM provider abstraction — Anthropic and Google backends.
Swap provider via DYNAMO_LLM_PROVIDER env var only.
Missing API key → warning + _NullProvider (degraded mode, not startup failure).

complete() returns LLMRawResponse instead of a plain string so that the
MeteringLLMProvider decorator can capture token counts and thinking data
without making a second API call.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)


@dataclass
class LLMRawResponse:
    """
    Rich response from an LLM provider.
    text is the generated content; all other fields feed into metering.
    """

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    thinking_summary: str | None = None  # first 500 chars of thinking block (audit)
    model: str = ""


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system_prompt: str, user_prompt: str) -> LLMRawResponse:
        """Return a rich response; .text is the generated content."""


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int, timeout: float):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMRawResponse:
        t0 = time.monotonic()
        msg = await asyncio.wait_for(
            self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            ),
            timeout=self._timeout,
        )

        # Extract text and thinking from content blocks
        text = ""
        thinking_summary: str | None = None
        thinking_tokens = 0
        for block in msg.content:
            if block.type == "thinking":
                thinking_tokens = getattr(block, "thinking_tokens", 0) or 0
                thinking_summary = (block.thinking or "")[:500] or None
            elif block.type == "text":
                text = block.text.strip()

        usage = msg.usage
        return LLMRawResponse(
            text=text,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            thinking_tokens=thinking_tokens,
            thinking_summary=thinking_summary,
            model=msg.model,
        )


class GoogleProvider(LLMProvider):
    """Uses google-generativeai. Import is lazy so app starts without it."""

    def __init__(self, api_key: str, model: str, max_tokens: int, timeout: float):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMRawResponse:
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

        # Extract token counts from usage_metadata when available
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage, "candidates_token_count", 0) or 0

        return LLMRawResponse(
            text=response.text.strip(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            thinking_tokens=0,
            thinking_summary=None,
            model=self._model,
        )


class _NullProvider(LLMProvider):
    """Returned when no API key is configured. Always returns empty response."""

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMRawResponse:
        log.warning("llm_provider.null_complete_called")
        return LLMRawResponse(text="")


def strip_markdown_json(text: str) -> str:
    """
    Strip markdown code fences that models sometimes include despite instructions.
    Handles ```json ... ``` and ``` ... ``` wrappers.
    """
    import re
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
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
