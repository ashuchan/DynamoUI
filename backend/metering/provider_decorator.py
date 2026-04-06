"""
MeteringLLMProvider — decorator that wraps any LLMProvider and automatically
records every LLM call into the metering subsystem.

Usage at startup:
    base_provider = create_provider(llm_settings)
    metered_provider = MeteringLLMProvider(
        inner=base_provider,
        metering_service=metering_service,
        provider_name=llm_settings.provider,
        model=llm_settings.anthropic_model,  # or google_model
    )

Callers set a MeteringContext via set_metering_context() before each LLM call.
The decorator reads the context automatically — no changes needed to
QuerySynthesiser, PatternSeeder, or any future caller.

If no MeteringContext is active (e.g. in tests), the call is passed through
and no metering record is written.
"""
from __future__ import annotations

import time
import uuid

import structlog

from backend.metering.context import get_metering_context
from backend.skill_registry.llm.provider import LLMProvider, LLMRawResponse

log = structlog.get_logger(__name__)


class MeteringLLMProvider(LLMProvider):
    """
    Transparent wrapper around any LLMProvider.
    Captures token counts, latency, and thinking data after each call,
    then delegates to MeteringService.record_llm_interaction().
    """

    def __init__(
        self,
        inner: LLMProvider,
        metering_service: "MeteringService",  # noqa: F821
        provider_name: str,
        model: str,
    ) -> None:
        self._inner = inner
        self._metering = metering_service
        self._provider_name = provider_name
        self._model = model

    async def complete(self, system_prompt: str, user_prompt: str) -> LLMRawResponse:
        ctx = get_metering_context()
        t0 = time.monotonic()
        error_message: str | None = None
        response: LLMRawResponse | None = None

        try:
            response = await self._inner.complete(system_prompt, user_prompt)
            return response
        except Exception as exc:
            error_message = str(exc)[:1000]
            raise
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            if ctx is not None:
                await self._record(ctx, response, latency_ms, error_message)

    async def _record(
        self,
        ctx: "MeteringContext",  # noqa: F821
        response: LLMRawResponse | None,
        latency_ms: int,
        error_message: str | None,
    ) -> None:
        """Fire-and-forget metering write — never raises."""
        from backend.metering.dto.interaction_dto import LLMInteractionCreateDTO

        success = error_message is None and response is not None

        prompt_tokens = response.prompt_tokens if response else 0
        completion_tokens = response.completion_tokens if response else 0
        thinking_tokens = response.thinking_tokens if response else 0
        total_tokens = prompt_tokens + completion_tokens + thinking_tokens
        thinking_summary = response.thinking_summary if response else None
        # Use the model echoed by the provider when available (Anthropic returns it)
        model = (response.model if response and response.model else self._model)

        dto = LLMInteractionCreateDTO(
            id=uuid.uuid4(),
            operation_id=ctx.operation_id,
            tenant_id=ctx.tenant_id,
            interaction_type=ctx.interaction_type,
            provider=self._provider_name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            thinking_tokens=thinking_tokens,
            total_tokens=total_tokens,
            thinking_summary=thinking_summary,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message,
        )

        try:
            await self._metering.record_llm_interaction(dto)
        except Exception as exc:
            log.warning(
                "metering_provider.record_failed",
                operation_id=str(ctx.operation_id),
                error=str(exc),
            )
