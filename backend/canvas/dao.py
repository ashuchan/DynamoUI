"""CanvasSessionDAO — async CRUD for canvas_sessions table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.canvas.models.session import (
    CanvasMessage,
    ConversationState,
    canvas_sessions,
)


class CanvasSessionNotFound(Exception):
    pass


def _coerce_session_id(session_id: UUID | str) -> UUID | None:
    """Coerce a path/segment value to UUID. Returns None on a malformed string
    so the caller can map to 404 instead of bubbling a 500."""
    if isinstance(session_id, UUID):
        return session_id
    try:
        return UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        return None


class CanvasSessionDAO:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        *,
        operator_id: UUID,
        tenant_id: UUID,
        skill_yaml_context: str = "",
        opening_message: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        sid = uuid4()
        messages: list[dict[str, Any]] = []
        if opening_message:
            messages.append(
                CanvasMessage(
                    role="assistant",
                    content=opening_message,
                    timestamp=now.isoformat(),
                ).model_dump()
            )
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(canvas_sessions).values(
                    session_id=sid,
                    operator_id=operator_id,
                    tenant_id=tenant_id,
                    state=ConversationState.ELICITING.value,
                    messages=messages,
                    partial_intent={},
                    skill_yaml_context=skill_yaml_context,
                    created_at=now,
                    updated_at=now,
                )
            )
        return await self.get(sid, operator_id=operator_id, tenant_id=tenant_id)

    async def get(
        self,
        session_id: UUID | str,
        *,
        operator_id: UUID,
        tenant_id: UUID | None = None,
    ) -> dict[str, Any]:
        sid = _coerce_session_id(session_id)
        if sid is None:
            raise CanvasSessionNotFound(str(session_id))
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(canvas_sessions).where(
                        *self._scope_clauses(sid, operator_id, tenant_id),
                    )
                )
            ).mappings().first()
        if row is None:
            raise CanvasSessionNotFound(str(session_id))
        return dict(row)

    async def append_message(
        self,
        session_id: UUID | str,
        message: CanvasMessage,
        *,
        operator_id: UUID,
        tenant_id: UUID | None = None,
    ) -> None:
        sid = _coerce_session_id(session_id)
        if sid is None:
            raise CanvasSessionNotFound(str(session_id))
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(canvas_sessions.c.messages).where(
                        *self._scope_clauses(sid, operator_id, tenant_id),
                    )
                )
            ).first()
            if row is None:
                raise CanvasSessionNotFound(str(session_id))
            messages = list(row[0] or [])
            messages.append(message.model_dump())
            await conn.execute(
                sa.update(canvas_sessions)
                .where(*self._scope_clauses(sid, operator_id, tenant_id))
                .values(
                    messages=messages,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    async def update_state(
        self,
        session_id: UUID | str,
        *,
        operator_id: UUID,
        tenant_id: UUID | None = None,
        state: ConversationState | None = None,
        partial_intent: dict[str, Any] | None = None,
        preview_cache: dict[str, Any] | None = None,
        preview_cache_key: str | None = None,
    ) -> None:
        sid = _coerce_session_id(session_id)
        if sid is None:
            raise CanvasSessionNotFound(str(session_id))
        values: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
        if state is not None:
            values["state"] = state.value
        if partial_intent is not None:
            values["partial_intent"] = partial_intent
        if preview_cache is not None:
            values["preview_cache"] = preview_cache
        if preview_cache_key is not None:
            values["preview_cache_key"] = preview_cache_key
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(canvas_sessions)
                .where(*self._scope_clauses(sid, operator_id, tenant_id))
                .values(**values)
            )
            if result.rowcount == 0:
                raise CanvasSessionNotFound(str(session_id))

    async def turn_writeback(
        self,
        session_id: UUID | str,
        *,
        operator_id: UUID,
        tenant_id: UUID | None = None,
        appended_messages: list[CanvasMessage],
        state: ConversationState | None = None,
        partial_intent: dict[str, Any] | None = None,
    ) -> None:
        """Atomic post-LLM writeback: append messages + update state in one tx.

        Avoids the gap where an append could persist while the matching state
        update fails, leaving messages and intent out of sync.
        """
        sid = _coerce_session_id(session_id)
        if sid is None:
            raise CanvasSessionNotFound(str(session_id))
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.select(canvas_sessions.c.messages).where(
                        *self._scope_clauses(sid, operator_id, tenant_id),
                    )
                )
            ).first()
            if row is None:
                raise CanvasSessionNotFound(str(session_id))
            messages = list(row[0] or [])
            for msg in appended_messages:
                messages.append(msg.model_dump())
            values: dict[str, Any] = {
                "messages": messages,
                "updated_at": datetime.now(timezone.utc),
            }
            if state is not None:
                values["state"] = state.value
            if partial_intent is not None:
                values["partial_intent"] = partial_intent
            await conn.execute(
                sa.update(canvas_sessions)
                .where(*self._scope_clauses(sid, operator_id, tenant_id))
                .values(**values)
            )

    async def message_count(
        self,
        session_id: UUID | str,
        *,
        operator_id: UUID,
        tenant_id: UUID | None = None,
    ) -> int:
        sid = _coerce_session_id(session_id)
        if sid is None:
            raise CanvasSessionNotFound(str(session_id))
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(canvas_sessions.c.messages).where(
                        *self._scope_clauses(sid, operator_id, tenant_id),
                    )
                )
            ).first()
        if row is None:
            raise CanvasSessionNotFound(str(session_id))
        return len(row[0] or [])

    @staticmethod
    def _scope_clauses(
        sid: UUID, operator_id: UUID, tenant_id: UUID | None
    ) -> list[Any]:
        clauses: list[Any] = [
            canvas_sessions.c.session_id == sid,
            canvas_sessions.c.operator_id == operator_id,
        ]
        if tenant_id is not None:
            clauses.append(canvas_sessions.c.tenant_id == tenant_id)
        return clauses
