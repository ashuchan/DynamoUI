"""
MeteringContext — carries operation_id and interaction_type through async call
chains without threading them through every function signature.

Usage:
    token = set_metering_context(MeteringContext(
        operation_id=some_uuid,
        interaction_type="query_synthesis",
    ))
    try:
        await do_work()   # MeteringLLMProvider reads context here automatically
    finally:
        clear_metering_context(token)
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class MeteringContext:
    operation_id: UUID
    interaction_type: str
    tenant_id: UUID | None = None


_current_metering: ContextVar[MeteringContext | None] = ContextVar(
    "_current_metering", default=None
)


def set_metering_context(ctx: MeteringContext) -> Token:
    """Set the active metering context. Returns a reset token for cleanup."""
    return _current_metering.set(ctx)


def get_metering_context() -> MeteringContext | None:
    """Return the active metering context, or None if not set."""
    return _current_metering.get()


def clear_metering_context(token: Token) -> None:
    """Reset the context to the state before set_metering_context() was called."""
    _current_metering.reset(token)
