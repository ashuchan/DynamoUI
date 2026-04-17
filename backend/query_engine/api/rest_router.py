"""v2 resolution router.

POST /api/v1/resolve/v2       — new discriminated-union ResolutionResult
POST /api/v1/resolve/edit     — reverse NL translation
POST /api/v1/commands/dispatch — slash command dispatcher
GET  /api/v1/search           — universal search

The legacy /api/v1/resolve endpoint remains unchanged so old clients keep
working; new clients are expected to move to /resolve/v2. The provenance
envelope is the same either way.
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.query_engine.pipeline import ResolvePipeline
from backend.query_engine.provenance import ExecutedResult

log = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared dependencies
# ---------------------------------------------------------------------------


def _get_pipeline(request: Request) -> ResolvePipeline:
    pipe: ResolvePipeline | None = getattr(request.app.state, "resolve_pipeline", None)
    if pipe is None:
        raise HTTPException(status_code=503, detail="resolve pipeline not initialised")
    return pipe


# ---------------------------------------------------------------------------
# /resolve/v2 — discriminated-union ResolutionResult
# ---------------------------------------------------------------------------


class ResolveRequestV2(BaseModel):
    input: str
    sessionContext: dict | None = None


class ResolutionExecuted(BaseModel):
    kind: Literal["executed"] = "executed"
    executed: ExecutedResult


class ResolutionClarification(BaseModel):
    kind: Literal["clarification_needed"] = "clarification_needed"
    question: str
    candidates: list[dict] = Field(default_factory=list)


@router.post("/resolve/v2", summary="v2 NL resolve — returns a discriminated-union")
async def resolve_v2(
    body: ResolveRequestV2,
    request: Request,
    pipeline: Annotated[ResolvePipeline, Depends(_get_pipeline)],
) -> dict:
    if len(body.input) > 500:
        raise HTTPException(status_code=400, detail="Input exceeds 500 character limit")

    input_hash = hashlib.sha256(body.input.encode()).hexdigest()[:16]
    log.info("api.resolve_v2.request", input_hash=input_hash, length=len(body.input))

    # v2 of the plan lists five kinds: executed, schedule_draft, alert_draft,
    # mutation_preview, clarification_needed. Only executed and clarification
    # are implemented in the pipeline today — the other kinds are produced by
    # feature-specific paths (NL-to-schedule etc.) and are wired in via their
    # own routers. Everything else is an "executed" or a graceful fall-back.
    result = await pipeline.resolve(user_input=body.input)
    if result is None:
        return ResolutionClarification(
            question=(
                "I couldn't resolve that to a known pattern or synthesise a "
                "plan. Could you restate what you're looking for?"
            )
        ).model_dump()
    return ResolutionExecuted(executed=result).model_dump()


# ---------------------------------------------------------------------------
# /resolve/edit — reverse translate a plan to NL
# ---------------------------------------------------------------------------


class EditRequest(BaseModel):
    queryPlan: dict


@router.post("/resolve/edit", summary="Reverse-translate a QueryPlan to NL")
async def resolve_edit(body: EditRequest, request: Request) -> dict:
    """Heuristic first; will call the LLM if the plan was synthesised originally.

    For v2 we implement the cheap deterministic path — it's enough for cache
    hits and saved views where we already stored the original NL input.
    """
    plan = body.queryPlan or {}
    entity = plan.get("entity") or "records"
    filters = plan.get("filters") or []
    parts: list[str] = [f"all {entity.lower()}s"] if not filters else [entity.lower()]

    for f in filters:
        field = f.get("field")
        op = f.get("op", "eq")
        value = f.get("value")
        if op == "eq":
            parts.append(f"where {field} is {value}")
        elif op == "gt":
            parts.append(f"where {field} > {value}")
        elif op == "lt":
            parts.append(f"where {field} < {value}")
        elif op == "like":
            parts.append(f"where {field} contains {value}")
        else:
            parts.append(f"where {field} {op} {value}")

    limit = plan.get("result_limit")
    if limit:
        parts.insert(0, f"top {limit}")

    return {"nlInput": " ".join(parts).strip()}


# ---------------------------------------------------------------------------
# /commands/dispatch
# ---------------------------------------------------------------------------


class DispatchRequest(BaseModel):
    command: str
    args: str = ""
    sessionContext: dict | None = None


@router.post("/commands/dispatch", summary="Slash-command dispatcher")
async def commands_dispatch(
    body: DispatchRequest,
    request: Request,
    pipeline: Annotated[ResolvePipeline, Depends(_get_pipeline)],
) -> dict:
    """Maps /chart, /table, /schedule, /save, /pin, /export to the right flow."""
    cmd = body.command.lower().strip("/ ")
    args = body.args.strip()

    if cmd in ("chart", "table"):
        # Forces VISUALIZE or READ intent. In v2 we re-use the resolve pipeline
        # and let the downstream UI decide which renderer to use based on
        # result_shape.
        result = await pipeline.resolve(user_input=args)
        if result is None:
            return ResolutionClarification(
                question=f"I couldn't resolve '{args}' into a {cmd}."
            ).model_dump()
        return ResolutionExecuted(executed=result).model_dump()

    if cmd == "schedule":
        # Delegates to the scheduling NL parser when configured.
        try:
            from backend.scheduling.nl_parser import parse_schedule_nl  # lazy
        except ImportError:
            raise HTTPException(status_code=501, detail="scheduling module unavailable")
        draft = await parse_schedule_nl(args, request.app)
        return {"kind": "schedule_draft", "draft": draft.model_dump(), "provenance": None}

    if cmd in ("save", "pin", "export"):
        # These need a session context from the last query — returned as hint.
        return {
            "kind": "clarification_needed",
            "question": f"/{cmd} requires a selected result. Send sessionContext from the last query.",
            "candidates": [],
        }

    raise HTTPException(status_code=400, detail=f"unknown command /{cmd}")


# ---------------------------------------------------------------------------
# /search — universal search
# ---------------------------------------------------------------------------


@router.get("/search", summary="Universal search across entities, views, dashboards")
async def universal_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    types: str = Query("entity,saved_view,dashboard,widget,pattern"),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Fast RapidFuzz-style matching across the available resource catalogs.

    Only resource types present in the app are searched — e.g. if the
    personalisation feature is off, saved_view/dashboard results won't appear.
    """
    wanted = {t.strip() for t in types.split(",") if t.strip()}
    results: list[dict] = []

    registry = request.app.state.skill_registry
    if "entity" in wanted:
        needle = q.lower()
        for name, skill in registry.entity_by_name.items():
            score = _fuzzy_score(needle, name.lower())
            if score > 0.4:
                results.append(
                    {"type": "entity", "id": name, "name": name, "score": score}
                )

    if "pattern" in wanted:
        pattern_cache = getattr(request.app.state, "pattern_cache", None)
        if pattern_cache is not None:
            hit = pattern_cache.lookup(q)
            if hit is not None and hit.pattern_id:
                results.append(
                    {
                        "type": "pattern",
                        "id": hit.pattern_id,
                        "name": hit.matched_trigger or hit.pattern_id,
                        "score": hit.confidence or 0.0,
                        "entity": hit.entity,
                    }
                )

    personalisation = getattr(request.app.state, "personalisation_service", None)
    if personalisation is not None:
        if "saved_view" in wanted:
            results.extend(await personalisation.search_saved_views(q, limit=limit))
        if "dashboard" in wanted:
            results.extend(await personalisation.search_dashboards(q, limit=limit))

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"results": results[:limit]}


def _fuzzy_score(needle: str, hay: str) -> float:
    if not needle or not hay:
        return 0.0
    if needle == hay:
        return 1.0
    if needle in hay:
        return 0.8 + (len(needle) / max(1, len(hay))) * 0.2
    # character overlap heuristic
    common = set(needle) & set(hay)
    return len(common) / max(1, len(set(needle) | set(hay)))
