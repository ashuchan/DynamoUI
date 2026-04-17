"""Share tokens + embed routes + patterns/propose."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.auth.api.dependencies import AuthContext, get_current_context
from backend.sharing.services.share_service import (
    ShareExpired,
    ShareService,
    ShareTokenNotFound,
)

router = APIRouter()


def _svc(request: Request) -> ShareService:
    svc: ShareService | None = getattr(request.app.state, "share_service", None)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sharing subsystem unavailable",
        )
    return svc


# ---------------------------------------------------------------------------
# Token CRUD
# ---------------------------------------------------------------------------


class ShareTokenCreate(BaseModel):
    sourceType: str
    sourceId: str
    expiresInSeconds: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 365)
    maxAccessCount: int | None = Field(default=None, ge=1, le=100000)


@router.post("/share-tokens", status_code=201)
async def create_share_token(
    payload: ShareTokenCreate,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    return await _svc(request).create(
        source_type=payload.sourceType,
        source_id=payload.sourceId,
        user_id=ctx.user.id,
        tenant_id=ctx.tenant.id,
        expires_in_seconds=payload.expiresInSeconds,
        max_access_count=payload.maxAccessCount,
    )


@router.get("/share-tokens")
async def list_share_tokens(
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
    sourceType: str | None = Query(None),
    sourceId: str | None = Query(None),
) -> list[dict]:
    return await _svc(request).list(
        source_type=sourceType, source_id=sourceId, user_id=ctx.user.id
    )


@router.delete("/share-tokens/{token_id}", status_code=204)
async def delete_share_token(
    token_id: UUID,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
):
    await _svc(request).delete(token_id, user_id=ctx.user.id)


# ---------------------------------------------------------------------------
# Public (no auth) paths
# ---------------------------------------------------------------------------


@router.get("/shared/{token}")
async def resolve_share(token: str, request: Request) -> dict:
    """Public endpoint — renders a saved view's contents using the creator's identity."""
    try:
        resolved = await _svc(request).resolve(token)
    except ShareTokenNotFound:
        raise HTTPException(status_code=404, detail="invalid or unknown token")
    except ShareExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc))
    # For v2 we return the resolved source pointer; actually executing the
    # view publicly would need a system-role resolver that isn't wired yet.
    return {"sourceType": resolved["sourceType"], "sourceId": resolved["sourceId"]}


@router.get("/embed/{token}", response_class=HTMLResponse)
async def embed_share(token: str, request: Request) -> str:
    try:
        resolved = await _svc(request).resolve(token)
    except (ShareTokenNotFound, ShareExpired):
        return HTMLResponse("<h1>Share link expired</h1>", status_code=410)
    return HTMLResponse(
        f"""<!doctype html><html><head><title>DynamoUI embed</title></head>
        <body><div id=\"root\" data-source-type=\"{resolved['sourceType']}\"
        data-source-id=\"{resolved['sourceId']}\"></div></body></html>"""
    )


# ---------------------------------------------------------------------------
# Patterns (propose)
# ---------------------------------------------------------------------------


class PatternProposeRequest(BaseModel):
    queryPlan: dict
    userInput: str


@router.post("/patterns/propose")
async def propose_pattern(
    payload: PatternProposeRequest,
    request: Request,
    ctx: Annotated[AuthContext, Depends(get_current_context)],
) -> dict:
    """End-user proposes a successful query as a new pattern.

    Delegates to the existing PatternPromoter's review-queue path. Requires
    the pattern_promoter to be wired at startup; degrades gracefully.
    """
    promoter = getattr(request.app.state, "pattern_promoter", None)
    if promoter is None:
        raise HTTPException(status_code=503, detail="promoter not available")

    # Re-use the normal promotion flow, but force the review-queue path.
    from backend.adapters.base import (
        AggregationClause,
        FilterClause,
        JoinClause,
        QueryPlan,
        SortClause,
    )

    plan = QueryPlan(
        entity=payload.queryPlan.get("entity", ""),
        filters=[FilterClause(**f) for f in payload.queryPlan.get("filters", [])],
        sort=[SortClause(**s) for s in payload.queryPlan.get("sort", [])],
        joins=[JoinClause(**j) for j in payload.queryPlan.get("joins", [])],
        aggregations=[AggregationClause(**a) for a in payload.queryPlan.get("aggregations", [])],
        group_by=list(payload.queryPlan.get("group_by") or []),
        result_limit=payload.queryPlan.get("result_limit"),
    )

    # Force a sub-auto-promote confidence so the promoter routes to the
    # review queue rather than writing directly to the patterns YAML.
    import asyncio

    asyncio.create_task(
        promoter.promote(
            user_input=payload.userInput,
            query_plan=plan,
            confidence=0.91,  # review-queue range
            entity=plan.entity,
        )
    )
    return {"proposalId": "queued", "status": "queued"}
