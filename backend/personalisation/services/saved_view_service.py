"""SavedView service — CRUD + schema-drift detection + execute-via-pipeline.

The "execute" path uses the resolve pipeline to preserve provenance semantics:
saved views get ``candidateSource='saved_view'`` in the envelope so the
frontend renders the right badge.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.adapters.base import QueryPlan
from backend.adapters.registry import get_adapter_registry
from backend.personalisation.models.dtos import (
    SavedViewCreate,
    SavedViewRead,
    SavedViewUpdate,
)
from backend.personalisation.models.tables import saved_views
from backend.query_engine.provenance import (
    ExecutedResult,
    build_provenance,
    compute_skill_hash,
)
from backend.query_engine.verifier.llm_verifier import LLMVerifier
from backend.query_engine.verifier.verdict import CandidateResolution
from backend.skill_registry.config.settings import FeatureFlagSettings
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)


class SavedViewNotFound(Exception):
    pass


class SavedViewService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        registry: SkillRegistry,
        verifier: LLMVerifier,
        features: FeatureFlagSettings,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._verifier = verifier
        self._features = features

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(
        self, *, owner_id: UUID, tenant_id: UUID, payload: SavedViewCreate
    ) -> SavedViewRead:
        row_id = uuid4()
        skill_hash = compute_skill_hash(self._registry)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(saved_views).values(
                    id=row_id,
                    owner_user_id=owner_id,
                    tenant_id=tenant_id,
                    name=payload.name,
                    nl_input=payload.nlInput,
                    query_plan_json=payload.queryPlan,
                    entity=payload.entity,
                    result_shape=payload.resultShape,
                    is_shared=payload.isShared,
                    pattern_id_hint=payload.patternIdHint,
                    skill_hash=skill_hash,
                )
            )
        return await self.get(row_id, owner_id=owner_id)

    async def list(self, *, owner_id: UUID, entity: str | None = None, shared: bool = False) -> list[SavedViewRead]:
        async with self._engine.connect() as conn:
            stmt = sa.select(saved_views).where(
                sa.or_(
                    saved_views.c.owner_user_id == owner_id,
                    sa.and_(saved_views.c.is_shared == True, sa.literal(shared)),
                )
            )
            if entity:
                stmt = stmt.where(saved_views.c.entity == entity)
            rows = (await conn.execute(stmt)).mappings().all()
        return [_row_to_read(r) for r in rows]

    async def get(self, view_id: UUID, *, owner_id: UUID | None = None) -> SavedViewRead:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(saved_views).where(saved_views.c.id == view_id)
                )
            ).mappings().first()
        if row is None:
            raise SavedViewNotFound(str(view_id))
        if owner_id is not None and row["owner_user_id"] != owner_id and not row["is_shared"]:
            raise SavedViewNotFound(str(view_id))
        return _row_to_read(row)

    async def update(
        self, view_id: UUID, *, owner_id: UUID, payload: SavedViewUpdate
    ) -> SavedViewRead:
        values = {k: v for k, v in payload.model_dump().items() if v is not None}
        if values:
            mapped = {}
            if "name" in values:
                mapped["name"] = values["name"]
            if "isShared" in values:
                mapped["is_shared"] = values["isShared"]
            mapped["updated_at"] = sa.func.now()
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(saved_views)
                    .where(
                        saved_views.c.id == view_id,
                        saved_views.c.owner_user_id == owner_id,
                    )
                    .values(**mapped)
                )
        return await self.get(view_id, owner_id=owner_id)

    async def delete(self, view_id: UUID, *, owner_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(saved_views).where(
                    saved_views.c.id == view_id,
                    saved_views.c.owner_user_id == owner_id,
                )
            )

    # ------------------------------------------------------------------
    # Execute — respects schema-drift policy from the v2 plan §2.2
    # ------------------------------------------------------------------

    async def execute(
        self, view_id: UUID, *, owner_id: UUID
    ) -> ExecutedResult:
        view = await self.get(view_id, owner_id=owner_id)
        current_hash = compute_skill_hash(self._registry)

        plan = _parse_plan(view.queryPlan, view.entity)
        skill = self._registry.entity_by_name.get(plan.entity)
        if skill is None:
            raise SavedViewNotFound(str(view_id))

        if view.skillHash != current_hash:
            # Schema changed: do NOT auto-re-resolve in v2 — mark stale and
            # surface to UI, per plan §2.2 step 3. The frontend decides.
            await self._mark_stale(view_id)

        candidate = CandidateResolution(
            source="saved_view",
            query_plan=plan,
            entity=plan.entity,
            intent="READ",
        )
        resolution = await self._verifier.verify(
            user_input=view.nlInput,
            candidate=candidate,
            registry=self._registry,
            skill_hash=current_hash,
            user_id=owner_id,
        )

        adapter = get_adapter_registry().get(skill.adapter)
        t0 = time.monotonic()
        try:
            exec_result = await adapter.execute_query(skill, resolution.effective_plan)
        except Exception as exc:
            log.warning("saved_view.execute_failed", error=str(exc), view_id=str(view_id))
            return ExecutedResult(
                result={"entity": plan.entity, "rows": [], "totalCount": 0, "error": str(exc)},
                provenance=build_provenance(
                    resolution=resolution,
                    execution_latency_ms=0,
                    skill_hash=current_hash,
                    adapter_key=skill.adapter,
                    generated_sql=None,
                    expose_sql=self._features.expose_sql,
                ),
            )
        ms = int((time.monotonic() - t0) * 1000)

        return ExecutedResult(
            result={
                "entity": plan.entity,
                "rows": exec_result.rows,
                "totalCount": exec_result.total_count,
                "stale": view.stale,
                "queryTimeMs": ms,
            },
            provenance=build_provenance(
                resolution=resolution,
                execution_latency_ms=ms,
                skill_hash=current_hash,
                adapter_key=skill.adapter,
                generated_sql=None,
                expose_sql=self._features.expose_sql,
            ),
        )

    async def _mark_stale(self, view_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(saved_views)
                .where(saved_views.c.id == view_id)
                .values(stale=True, updated_at=sa.func.now())
            )

    # ------------------------------------------------------------------
    # Search (for /api/v1/search)
    # ------------------------------------------------------------------

    async def search(self, q: str, *, owner_id: UUID, limit: int = 20) -> list[dict]:
        qlow = q.lower()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(saved_views.c.id, saved_views.c.name, saved_views.c.entity)
                    .where(
                        sa.or_(
                            saved_views.c.owner_user_id == owner_id,
                            saved_views.c.is_shared == True,
                        ),
                        sa.func.lower(saved_views.c.name).like(f"%{qlow}%"),
                    )
                    .limit(limit)
                )
            ).mappings().all()
        return [
            {
                "type": "saved_view",
                "id": str(r["id"]),
                "name": r["name"],
                "entity": r["entity"],
                "score": 0.75,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_read(row: sa.engine.RowMapping) -> SavedViewRead:
    layout = row["query_plan_json"]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return SavedViewRead(
        id=row["id"],
        ownerUserId=row["owner_user_id"],
        name=row["name"],
        nlInput=row["nl_input"],
        queryPlan=layout or {},
        entity=row["entity"],
        resultShape=row["result_shape"],
        isShared=row["is_shared"],
        patternIdHint=row["pattern_id_hint"],
        skillHash=row["skill_hash"],
        stale=row["stale"],
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _parse_plan(data: dict, entity: str) -> QueryPlan:
    from backend.adapters.base import (
        AggregationClause,
        FilterClause,
        JoinClause,
        SortClause,
    )

    return QueryPlan(
        entity=data.get("entity") or entity,
        filters=[FilterClause(**f) for f in (data.get("filters") or [])],
        sort=[SortClause(**s) for s in (data.get("sort") or [])],
        joins=[JoinClause(**j) for j in (data.get("joins") or [])],
        aggregations=[AggregationClause(**a) for a in (data.get("aggregations") or [])],
        group_by=list(data.get("group_by") or []),
        result_limit=data.get("result_limit"),
        page=int(data.get("page", 1) or 1),
        page_size=int(data.get("page_size", 25) or 25),
        select_fields=list(data.get("select_fields") or []),
    )
