"""Canvas REST router — mounted at /api/v1/canvas.

Endpoint table (LLD §4):

  POST   /canvas/session
  GET    /canvas/session/{id}
  POST   /canvas/session/{id}/message
  GET    /canvas/session/{id}/intent
  GET    /canvas/session/{id}/preview
  POST   /canvas/session/{id}/generate
  GET    /canvas/session/{id}/artifacts
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import Response as FastAPIResponse

from backend.canvas.api.dependencies import CanvasAuthContext, get_canvas_context
from backend.canvas.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    GenerateResponse,
    IntentEnvelope,
    MessageResponse,
    PreviewData,
    SendMessageRequest,
    SessionView,
)
from backend.canvas.dao import CanvasSessionDAO, CanvasSessionNotFound
from backend.canvas.generator import CanvasGenerator
from backend.canvas.models.intent import CanvasIntent
from backend.canvas.models.session import CanvasMessage, ConversationState
from backend.canvas.preview.synthetic_data import PreviewBuilder
from backend.canvas.conversation.driver import ConversationDriver
from backend.skill_registry.config.settings import canvas_settings

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/canvas")


# ---------------------------------------------------------------------------
# Service accessors — populated at startup in main.py
# ---------------------------------------------------------------------------
def _dao(request: Request) -> CanvasSessionDAO:
    dao = getattr(request.app.state, "canvas_dao", None)
    if dao is None:
        raise HTTPException(503, "canvas subsystem unavailable")
    return dao


def _driver(request: Request) -> ConversationDriver:
    driver = getattr(request.app.state, "canvas_driver", None)
    if driver is None:
        raise HTTPException(503, "canvas subsystem unavailable")
    return driver


def _preview(request: Request) -> PreviewBuilder:
    pb = getattr(request.app.state, "canvas_preview", None)
    if pb is None:
        raise HTTPException(503, "canvas subsystem unavailable")
    return pb


def _generator(request: Request) -> CanvasGenerator:
    gen = getattr(request.app.state, "canvas_generator", None)
    if gen is None:
        raise HTTPException(503, "canvas subsystem unavailable")
    return gen


def _skill_lookup(request: Request) -> dict[str, dict[str, Any]]:
    """Map of entity_name → raw skill yaml dict for enrichment + preview.

    The SkillRegistry stores Pydantic skill objects; we serialise to dicts here
    because the enricher operates on plain dicts (mirrors the on-disk YAML).
    """
    registry = getattr(request.app.state, "skill_registry", None)
    if registry is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, skill in getattr(registry, "entity_by_name", {}).items():
        try:
            out[name] = skill.model_dump()
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_canvas_cookie(response: Response, ctx: CanvasAuthContext, raw_token: str | None) -> None:
    """Mirror the bearer JWT into the canvas cookie.

    Used so that a plain <a download> link to /artifacts can authenticate.
    Pulls the raw token from the auth context's claims via the same encoder
    helper to keep the artifact link working across browsers that block
    cross-tab Authorization headers.
    """
    if raw_token is None:
        return
    response.set_cookie(
        key=canvas_settings.cookie_name,
        value=raw_token,
        max_age=int(timedelta(hours=canvas_settings.session_ttl_hours).total_seconds()),
        httponly=True,
        secure=canvas_settings.cookie_secure,
        samesite="lax",
        path="/api/v1/canvas",
    )


async def _load_intent(
    dao: CanvasSessionDAO,
    session_id: str | UUID,
    *,
    operator_id: UUID,
    tenant_id: UUID,
) -> tuple[dict[str, Any], CanvasIntent, ConversationState]:
    row = await dao.get(session_id, operator_id=operator_id, tenant_id=tenant_id)
    partial = dict(row["partial_intent"] or {})
    # Defensive: drop any session_id smuggled in by LLM-authored payloads.
    partial.pop("session_id", None)
    intent = CanvasIntent.model_validate({"session_id": str(row["session_id"]), **partial})
    state = ConversationState(row["state"])
    return row, intent, state


# ---------------------------------------------------------------------------
# POST /session
# ---------------------------------------------------------------------------
@router.post("/session", response_model=CreateSessionResponse)
async def create_session(
    request: Request,
    response: Response,
    body: CreateSessionRequest | None = None,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)] = None,  # type: ignore[assignment]
) -> CreateSessionResponse:
    dao = _dao(request)
    driver = _driver(request)
    skill_ctx = (body.skill_yaml_context if body else "") or ""
    opening = await driver.opening_message(skill_ctx)

    row = await dao.create(
        operator_id=ctx.user.id,
        tenant_id=ctx.tenant.id,
        skill_yaml_context=skill_ctx,
        opening_message=opening,
    )

    # Mirror the bearer token to a cookie so /artifacts works as a plain <a>.
    auth_header = request.headers.get("authorization", "")
    raw_token = auth_header.split(" ", 1)[1] if auth_header.lower().startswith("bearer ") else None
    if raw_token is None:
        raw_token = request.cookies.get(canvas_settings.cookie_name)
    _set_canvas_cookie(response, ctx, raw_token)

    return CreateSessionResponse(session_id=str(row["session_id"]))


# ---------------------------------------------------------------------------
# GET /session/{id}
# ---------------------------------------------------------------------------
@router.get("/session/{session_id}", response_model=SessionView)
async def get_session(
    request: Request,
    session_id: str,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> SessionView:
    dao = _dao(request)
    try:
        row = await dao.get(session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id)
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")
    return SessionView(
        session_id=str(row["session_id"]),
        state=ConversationState(row["state"]),
        messages=[CanvasMessage.model_validate(m) for m in (row["messages"] or [])],
        partial_intent=row["partial_intent"] or {},
    )


# ---------------------------------------------------------------------------
# POST /session/{id}/message
# ---------------------------------------------------------------------------
@router.post("/session/{session_id}/message", response_model=MessageResponse)
async def send_message(
    request: Request,
    session_id: str,
    body: SendMessageRequest,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> MessageResponse:
    dao = _dao(request)
    driver = _driver(request)
    try:
        row = await dao.get(session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id)
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")

    # Count only user-authored messages so the server-generated opening
    # assistant message doesn't eat one slot of the configured cap.
    user_message_count = sum(
        1 for m in (row["messages"] or []) if m.get("role") == "user"
    )
    if user_message_count >= canvas_settings.max_conversation_turns:
        raise HTTPException(409, "conversation turn cap reached")

    state = ConversationState(row["state"])
    if state == ConversationState.COMPLETE:
        raise HTTPException(409, "session already complete")

    now = datetime.now(timezone.utc).isoformat()
    user_msg = CanvasMessage(role="user", content=body.message, timestamp=now)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in (row["messages"] or [])
    ] + [{"role": "user", "content": body.message}]

    result = await driver.turn(
        user_message=body.message,
        history=history,
        partial_intent=row["partial_intent"] or {},
        state=state,
        session_id=session_id,
        skill_yaml_context=row["skill_yaml_context"] or "",
    )

    asst_msg = CanvasMessage(
        role="assistant",
        content=result.reply,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Single-transaction writeback so the user message, assistant reply, and
    # state/partial_intent updates either all land or none do.
    await dao.turn_writeback(
        session_id,
        operator_id=ctx.user.id,
        tenant_id=ctx.tenant.id,
        appended_messages=[user_msg, asst_msg],
        state=result.new_state,
        partial_intent=result.new_partial,
    )

    return MessageResponse(
        reply=result.reply,
        intent_update=result.intent_update,
        session_state=result.new_state,
    )


# ---------------------------------------------------------------------------
# GET /session/{id}/intent
# ---------------------------------------------------------------------------
@router.get("/session/{session_id}/intent", response_model=IntentEnvelope)
async def get_intent(
    request: Request,
    session_id: str,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> IntentEnvelope:
    dao = _dao(request)
    try:
        row = await dao.get(session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id)
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")
    return IntentEnvelope(
        intent=row["partial_intent"] or {},
        state=ConversationState(row["state"]),
    )


# ---------------------------------------------------------------------------
# GET /session/{id}/preview
# ---------------------------------------------------------------------------
@router.get("/session/{session_id}/preview", response_model=PreviewData)
async def get_preview(
    request: Request,
    session_id: str,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> PreviewData:
    dao = _dao(request)
    builder = _preview(request)
    try:
        row, intent, _state = await _load_intent(
            dao, session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id
        )
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")

    cache_key = builder.cache_key(session_id, intent)
    if row.get("preview_cache_key") == cache_key and row.get("preview_cache"):
        return PreviewData.model_validate(row["preview_cache"])

    skills = _skill_lookup(request)
    skill_yaml = skills.get(intent.primary_entity) if intent.primary_entity else None
    preview = builder.build(
        session_id=session_id,
        intent=intent,
        skill_yaml=skill_yaml,
        enum_values=getattr(request.app.state, "canvas_enum_values", {}),
    )
    await dao.update_state(
        session_id,
        operator_id=ctx.user.id,
        tenant_id=ctx.tenant.id,
        preview_cache=preview.model_dump(),
        preview_cache_key=cache_key,
    )
    return preview


# ---------------------------------------------------------------------------
# POST /session/{id}/generate
# ---------------------------------------------------------------------------
@router.post("/session/{session_id}/generate", response_model=GenerateResponse)
async def generate(
    request: Request,
    session_id: str,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> GenerateResponse:
    dao = _dao(request)
    gen = _generator(request)
    try:
        row, intent, state = await _load_intent(
            dao, session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id
        )
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")

    if not intent.is_complete():
        raise HTTPException(422, "intent is incomplete; finish the conversation first")

    # Build the bundle around the primary entity, then layer in any extra
    # priorities. Without this, an entity_priorities list that omits the
    # primary entity would emit a layout pointing at a missing skill YAML.
    skills = _skill_lookup(request)
    target_skills: dict[str, dict[str, Any]] = {}
    if intent.primary_entity and intent.primary_entity in skills:
        target_skills[intent.primary_entity] = skills[intent.primary_entity]
    for name in intent.entity_priorities:
        if name in skills and name not in target_skills:
            target_skills[name] = skills[name]

    def _label_resolver(entity_name: str) -> str:
        s = skills.get(entity_name) or {}
        return s.get("label_plural") or s.get("label_singular") or entity_name

    result = gen.generate(
        session_id=session_id,
        intent=intent,
        skill_yamls=target_skills,
        entity_label_resolver=_label_resolver,
    )

    # Always reflect the latest state after generate.
    await dao.update_state(
        session_id,
        operator_id=ctx.user.id,
        tenant_id=ctx.tenant.id,
        state=ConversationState.COMPLETE,
    )

    return GenerateResponse(
        status="ok",
        files=result.files,
        artifacts_url=f"/api/v1/canvas/session/{session_id}/artifacts",
    )


# ---------------------------------------------------------------------------
# GET /session/{id}/artifacts
# ---------------------------------------------------------------------------
@router.get("/session/{session_id}/artifacts")
async def get_artifacts(
    request: Request,
    session_id: str,
    ctx: Annotated[CanvasAuthContext, Depends(get_canvas_context)],
) -> FastAPIResponse:
    dao = _dao(request)
    gen = _generator(request)
    try:
        await dao.get(session_id, operator_id=ctx.user.id, tenant_id=ctx.tenant.id)
    except CanvasSessionNotFound:
        raise HTTPException(404, "session not found")

    output_dir = gen._root / session_id  # noqa: SLF001 — internal accessor
    if not output_dir.exists():
        raise HTTPException(404, "no generated artifacts; call /generate first")
    blob = gen.zip_output(output_dir)
    return FastAPIResponse(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="canvas-output-{session_id}.zip"',
        },
    )
