"""Facade combining SavedView + Dashboard + Pin + Home composition.

Exposed on ``app.state.personalisation_service`` so other subsystems (eg.
scheduling, search) can depend on a single entry point.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from backend.personalisation.services.dashboard_service import (
    DashboardService,
    PinService,
)
from backend.personalisation.services.saved_view_service import SavedViewService


class PersonalisationService:
    def __init__(
        self,
        *,
        saved_views: SavedViewService,
        dashboards: DashboardService,
        pins: PinService,
    ) -> None:
        self.saved_views = saved_views
        self.dashboards = dashboards
        self.pins = pins

    async def compose_home(self, *, user_id: UUID) -> dict:
        pins = await self.pins.list(user_id=user_id)
        dashes = await self.dashboards.list(owner_id=user_id)
        default_dash = next((d for d in dashes if d.isDefault), None)
        views = await self.saved_views.list(owner_id=user_id)
        return {
            "pins": [p.model_dump() for p in pins],
            "defaultDashboard": default_dash.model_dump() if default_dash else None,
            "dashboards": [d.model_dump() for d in dashes],
            "recentSavedViews": [v.model_dump() for v in views[:10]],
            "upcomingSchedules": [],  # filled by scheduling service if wired
        }

    async def search_saved_views(self, q: str, *, limit: int = 20) -> list[dict]:
        # No owner filter here — caller-level routes already enforce ownership.
        # Used by the universal-search router.
        return []

    async def search_dashboards(self, q: str, *, limit: int = 20) -> list[dict]:
        return []
