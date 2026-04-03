"""
Widgets API router.
Widgets bypass the LLM entirely: Widget → PatternCache (direct by pattern_id) → Adapter → UI.
Total LLM calls for any widget execution: 0.

widgets.yaml schema:
  widgets:
    - id: active_employees
      title: Active Employees
      description: Show all currently active employees
      entity: Employee
      pattern_id: employee.active
      params: []
      category: HR
      icon: users
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = structlog.get_logger(__name__)

router = APIRouter()


def _get_widgets(request: Request) -> dict:
    return getattr(request.app.state, "widgets", {}) or {}


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class WidgetExecuteRequest(BaseModel):
    params: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/widgets", summary="All widgets + categories")
def list_widgets(request: Request) -> list[dict]:
    widgets = _get_widgets(request)
    items = widgets.get("widgets", []) if isinstance(widgets, dict) else []
    log.debug("api.widgets.list", count=len(items))
    return items


@router.get("/widgets/dashboard", summary="Widgets grouped by category")
def dashboard_widgets(request: Request) -> list[dict]:
    widgets = _get_widgets(request)
    items = widgets.get("widgets", []) if isinstance(widgets, dict) else []

    grouped: dict[str, list[dict]] = {}
    for w in items:
        cat = w.get("category", "Uncategorized")
        grouped.setdefault(cat, []).append(w)

    result = [{"category": cat, "widgets": ws} for cat, ws in grouped.items()]
    log.debug("api.widgets.dashboard", categories=len(result))
    return result


@router.get("/widgets/entity/{entity}", summary="Widgets for a specific entity")
def widgets_for_entity(entity: str, request: Request) -> list[dict]:
    widgets = _get_widgets(request)
    items = widgets.get("widgets", []) if isinstance(widgets, dict) else []
    entity_widgets = [w for w in items if w.get("entity") == entity]
    log.debug("api.widgets.by_entity", entity=entity, count=len(entity_widgets))
    return entity_widgets


@router.get("/widgets/{widget_id}", summary="Single widget definition")
def get_widget(widget_id: str, request: Request) -> dict:
    widgets = _get_widgets(request)
    items = widgets.get("widgets", []) if isinstance(widgets, dict) else []
    widget = next((w for w in items if w.get("id") == widget_id), None)
    if widget is None:
        raise HTTPException(status_code=404, detail=f"Widget {widget_id!r} not found")
    return widget


@router.post("/widgets/{widget_id}/execute", summary="Execute widget (0 LLM calls)")
async def execute_widget(
    widget_id: str,
    body: WidgetExecuteRequest,
    request: Request,
) -> dict:
    """
    Execute a widget's query directly via the PatternCache and Adapter.
    Widget clicks bypass the Intelligence Layer entirely — zero LLM calls.

    Flow: Widget → PatternCache (direct by pattern_id) → AdapterRegistry → PostgreSQLAdapter → result
    """
    from backend.adapters.base import FilterClause, QueryPlan, SortClause
    from backend.adapters.registry import get_adapter_registry
    from backend.skill_registry.models.registry import SkillRegistry
    import json

    widgets = _get_widgets(request)
    items = widgets.get("widgets", []) if isinstance(widgets, dict) else []
    widget = next((w for w in items if w.get("id") == widget_id), None)
    if widget is None:
        raise HTTPException(status_code=404, detail=f"Widget {widget_id!r} not found")

    entity_name: str = widget.get("entity", "")
    pattern_id: str = widget.get("pattern_id", "")

    registry: SkillRegistry = request.app.state.skill_registry
    skill = registry.entity_by_name.get(entity_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_name!r} not found")

    # Sanitise params before substitution
    provided_params = {
        k: _sanitise_param(v) for k, v in body.params.items()
    }

    # Resolve pattern template → QueryPlan
    pattern_cache = getattr(request.app.state, "pattern_cache", None)
    if pattern_cache is None:
        raise HTTPException(status_code=503, detail="Pattern cache not initialised")

    pattern = pattern_cache.get_pattern(pattern_id)
    if pattern is None:
        raise HTTPException(status_code=404, detail=f"Pattern {pattern_id!r} not found")

    try:
        query_template = pattern.get("query_template", "{}")
        # Substitute params into template
        for param_name, param_val in provided_params.items():
            query_template = query_template.replace(f"{{{param_name}}}", str(param_val))
        query_data = json.loads(query_template)
    except (json.JSONDecodeError, KeyError) as exc:
        log.error("api.widgets.template_parse_error", widget_id=widget_id, error=str(exc))
        raise HTTPException(status_code=400, detail=f"Widget template error: {exc}")

    # Build QueryPlan from parsed template
    filters = [
        FilterClause(field=f["field"], op=f["op"], value=f["value"])
        for f in query_data.get("filters", [])
    ]
    sort = [
        SortClause(field=s["field"], dir=s.get("dir", "asc"))
        for s in query_data.get("sort", [])
    ]
    plan = QueryPlan(
        entity=entity_name,
        filters=filters,
        sort=sort,
        page=1,
        page_size=skill.display.page_size,
    )

    adapter_reg = get_adapter_registry()
    adapter = adapter_reg.get(skill.adapter)
    if adapter is None:
        raise HTTPException(status_code=500, detail=f"Adapter {skill.adapter!r} not available")

    result = await adapter.execute_query(skill, plan)
    log.info(
        "api.widgets.executed",
        widget_id=widget_id,
        entity=entity_name,
        pattern_id=pattern_id,
        rows=len(result.rows),
    )

    return {
        "widgetId": widget_id,
        "entity": entity_name,
        "patternId": pattern_id,
        "rows": result.rows,
        "totalCount": result.total_count,
    }


def _sanitise_param(value: Any) -> Any:
    """
    Sanitise a widget parameter value before substitution into query templates.
    Rejects values containing SQL injection patterns.
    """
    if isinstance(value, str):
        # Strip characters that have no place in parameter values
        dangerous = [";", "--", "/*", "*/", "xp_", "EXEC", "exec"]
        for d in dangerous:
            if d.lower() in value.lower():
                raise HTTPException(
                    status_code=400,
                    detail=f"Parameter value contains disallowed content: {d!r}",
                )
    return value
