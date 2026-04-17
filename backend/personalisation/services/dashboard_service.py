"""Dashboard + tile + pin services."""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.personalisation.models.dtos import (
    DashboardCreate,
    DashboardLayout,
    DashboardRead,
    DashboardUpdate,
    PinCreate,
    PinRead,
    TileCreate,
    TileRead,
    TileUpdate,
)
from backend.personalisation.models.tables import (
    dashboard_tiles,
    dashboards,
    pins,
)


class DashboardNotFound(Exception):
    pass


class DashboardService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Dashboards
    # ------------------------------------------------------------------

    async def list(self, *, owner_id: UUID) -> list[DashboardRead]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(dashboards).where(dashboards.c.owner_user_id == owner_id)
                )
            ).mappings().all()
        return [_dashboard_read(r) for r in rows]

    async def create(
        self, *, owner_id: UUID, tenant_id: UUID, payload: DashboardCreate
    ) -> DashboardRead:
        row_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(dashboards).values(
                    id=row_id,
                    owner_user_id=owner_id,
                    tenant_id=tenant_id,
                    name=payload.name,
                    description=payload.description,
                    layout_json=payload.layout.model_dump(),
                )
            )
        return await self.get(row_id, owner_id=owner_id)

    async def get(self, dashboard_id: UUID, *, owner_id: UUID) -> DashboardRead:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(dashboards).where(
                        dashboards.c.id == dashboard_id,
                        dashboards.c.owner_user_id == owner_id,
                    )
                )
            ).mappings().first()
        if row is None:
            raise DashboardNotFound(str(dashboard_id))
        return _dashboard_read(row)

    async def get_tree(
        self, dashboard_id: UUID, *, owner_id: UUID
    ) -> dict:
        dash = await self.get(dashboard_id, owner_id=owner_id)
        async with self._engine.connect() as conn:
            tile_rows = (
                await conn.execute(
                    sa.select(dashboard_tiles).where(
                        dashboard_tiles.c.dashboard_id == dashboard_id
                    )
                )
            ).mappings().all()
        tiles = [_tile_read(r) for r in tile_rows]
        return {
            "dashboard": dash.model_dump(),
            "tiles": [t.model_dump() for t in tiles],
            "resolvedDisplayConfigs": {},
        }

    async def update(
        self, dashboard_id: UUID, *, owner_id: UUID, payload: DashboardUpdate
    ) -> DashboardRead:
        mapped: dict = {}
        if payload.name is not None:
            mapped["name"] = payload.name
        if payload.description is not None:
            mapped["description"] = payload.description
        if payload.layout is not None:
            _validate_layout(payload.layout)
            mapped["layout_json"] = payload.layout.model_dump()
        if mapped:
            mapped["updated_at"] = sa.func.now()
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(dashboards)
                    .where(
                        dashboards.c.id == dashboard_id,
                        dashboards.c.owner_user_id == owner_id,
                    )
                    .values(**mapped)
                )
        return await self.get(dashboard_id, owner_id=owner_id)

    async def delete(self, dashboard_id: UUID, *, owner_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(dashboard_tiles).where(
                    dashboard_tiles.c.dashboard_id == dashboard_id
                )
            )
            await conn.execute(
                sa.delete(dashboards).where(
                    dashboards.c.id == dashboard_id,
                    dashboards.c.owner_user_id == owner_id,
                )
            )

    # ------------------------------------------------------------------
    # Tiles
    # ------------------------------------------------------------------

    async def add_tile(
        self, dashboard_id: UUID, *, owner_id: UUID, payload: TileCreate
    ) -> TileRead:
        await self.get(dashboard_id, owner_id=owner_id)
        tile_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(dashboard_tiles).values(
                    id=tile_id,
                    dashboard_id=dashboard_id,
                    source_type=payload.sourceType,
                    source_id=payload.sourceId,
                    position_x=payload.positionX,
                    position_y=payload.positionY,
                    width=payload.width,
                    height=payload.height,
                    overrides_json=payload.overrides,
                )
            )
            row = (
                await conn.execute(
                    sa.select(dashboard_tiles).where(dashboard_tiles.c.id == tile_id)
                )
            ).mappings().first()
        return _tile_read(row)

    async def update_tile(
        self, dashboard_id: UUID, tile_id: UUID, *, owner_id: UUID, payload: TileUpdate
    ) -> TileRead:
        await self.get(dashboard_id, owner_id=owner_id)
        mapped = {
            "position_x": payload.positionX,
            "position_y": payload.positionY,
            "width": payload.width,
            "height": payload.height,
            "overrides_json": payload.overrides,
        }
        mapped = {k: v for k, v in mapped.items() if v is not None}
        if mapped:
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.update(dashboard_tiles)
                    .where(
                        dashboard_tiles.c.id == tile_id,
                        dashboard_tiles.c.dashboard_id == dashboard_id,
                    )
                    .values(**mapped)
                )
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(dashboard_tiles).where(dashboard_tiles.c.id == tile_id)
                )
            ).mappings().first()
        return _tile_read(row)

    async def delete_tile(
        self, dashboard_id: UUID, tile_id: UUID, *, owner_id: UUID
    ) -> None:
        await self.get(dashboard_id, owner_id=owner_id)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(dashboard_tiles).where(
                    dashboard_tiles.c.id == tile_id,
                    dashboard_tiles.c.dashboard_id == dashboard_id,
                )
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self, q: str, *, owner_id: UUID, limit: int = 20) -> list[dict]:
        qlow = q.lower()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(dashboards.c.id, dashboards.c.name)
                    .where(
                        dashboards.c.owner_user_id == owner_id,
                        sa.func.lower(dashboards.c.name).like(f"%{qlow}%"),
                    )
                    .limit(limit)
                )
            ).mappings().all()
        return [
            {"type": "dashboard", "id": str(r["id"]), "name": r["name"], "score": 0.75}
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


class PinService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list(self, *, user_id: UUID) -> list[PinRead]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    sa.select(pins).where(pins.c.user_id == user_id).order_by(pins.c.position)
                )
            ).mappings().all()
        return [_pin_read(r) for r in rows]

    async def create(
        self, *, user_id: UUID, tenant_id: UUID, payload: PinCreate
    ) -> PinRead:
        row_id = uuid4()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(pins).values(
                    id=row_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    source_type=payload.sourceType,
                    source_id=payload.sourceId,
                )
            )
            row = (
                await conn.execute(sa.select(pins).where(pins.c.id == row_id))
            ).mappings().first()
        return _pin_read(row)

    async def delete(self, pin_id: UUID, *, user_id: UUID) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(pins).where(pins.c.id == pin_id, pins.c.user_id == user_id)
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_layout(layout: DashboardLayout) -> None:
    seen: list[tuple[int, int, int, int]] = []
    for t in layout.tiles:
        x, y, w, h = int(t.get("x", 0)), int(t.get("y", 0)), int(t.get("w", 0)), int(t.get("h", 0))
        if w < 1 or w > 12:
            raise ValueError(f"tile width {w} outside [1,12]")
        if x + w > 12:
            raise ValueError(f"tile at x={x} w={w} overflows 12-column grid")
        for (sx, sy, sw, sh) in seen:
            if not (x + w <= sx or sx + sw <= x or y + h <= sy or sy + sh <= y):
                raise ValueError("tiles overlap")
        seen.append((x, y, w, h))


def _dashboard_read(row: sa.engine.RowMapping) -> DashboardRead:
    layout = row["layout_json"]
    if isinstance(layout, str):
        layout = json.loads(layout)
    return DashboardRead(
        id=row["id"],
        ownerUserId=row["owner_user_id"],
        name=row["name"],
        description=row["description"],
        isDefault=row["is_default"],
        layout=DashboardLayout(**(layout or {"tiles": []})) if layout else DashboardLayout(),
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _tile_read(row: sa.engine.RowMapping) -> TileRead:
    return TileRead(
        id=row["id"],
        dashboardId=row["dashboard_id"],
        sourceType=row["source_type"],
        sourceId=row["source_id"],
        position={
            "x": row["position_x"],
            "y": row["position_y"],
            "w": row["width"],
            "h": row["height"],
        },
        overrides=row["overrides_json"],
    )


def _pin_read(row: sa.engine.RowMapping) -> PinRead:
    return PinRead(
        id=row["id"],
        userId=row["user_id"],
        sourceType=row["source_type"],
        sourceId=row["source_id"],
        position=row["position"],
        createdAt=row["created_at"],
    )
