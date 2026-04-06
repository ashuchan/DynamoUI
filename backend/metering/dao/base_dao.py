"""BaseDAO — shared write-pool access for all metering DAOs."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

import structlog

log = structlog.get_logger(__name__)


class BaseDAO:
    """
    Provides a write engine to all child DAOs.
    All metering writes go through the write pool; reads are also
    routed through write to keep consistency simple for internal tables.
    """

    def __init__(self, write_engine: AsyncEngine) -> None:
        self._engine = write_engine
