"""
PostgreSQLEngine — async SQLAlchemy engine + connection pool management.
Maintains separate read and write engines per the security model:
  - dynamoui_reader for all SELECT queries
  - dynamoui_writer for mutations only
"""
from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = structlog.get_logger(__name__)


class PostgreSQLEngine:
    """
    Manages async SQLAlchemy engines for read and write connections.
    Pool settings are driven by DYNAMO_PG_* environment variables.
    """

    def __init__(self, settings: object) -> None:
        """
        settings: PostgreSQLSettings instance from config/settings.py
        """
        self._settings = settings
        self._read_engine: AsyncEngine | None = None
        self._write_engine: AsyncEngine | None = None

    @property
    def read_engine(self) -> AsyncEngine:
        if self._read_engine is None:
            raise RuntimeError("PostgreSQLEngine not initialised. Call initialise() first.")
        return self._read_engine

    @property
    def write_engine(self) -> AsyncEngine:
        if self._write_engine is None:
            raise RuntimeError("PostgreSQLEngine not initialised. Call initialise() first.")
        return self._write_engine

    async def initialise(self) -> None:
        """Create async engines for both read and write users."""
        s = self._settings

        connect_args: dict = {}
        if s.ssl_mode == "require":
            connect_args["ssl"] = "require"
        elif s.ssl_mode in ("verify-ca", "verify-full"):
            connect_args["ssl"] = "verify_full"

        self._read_engine = create_async_engine(
            s.read_url,
            pool_size=s.pool_size,
            max_overflow=s.max_overflow,
            pool_timeout=s.pool_timeout,
            pool_recycle=s.pool_recycle,
            echo=s.echo_sql,
            connect_args=connect_args,
        )
        log.info(
            "pg_engine.read_initialised",
            host=s.host,
            port=s.port,
            database=s.database,
            user=s.user,
            pool_size=s.pool_size,
        )

        self._write_engine = create_async_engine(
            s.write_url,
            pool_size=max(2, s.pool_size // 5),
            max_overflow=5,
            pool_timeout=s.pool_timeout,
            pool_recycle=s.pool_recycle,
            echo=s.echo_sql,
            connect_args=connect_args,
        )
        log.info(
            "pg_engine.write_initialised",
            user=s.write_user,
        )

    async def dispose(self) -> None:
        """Close all pool connections. Called on shutdown."""
        if self._read_engine:
            await self._read_engine.dispose()
            log.info("pg_engine.read_disposed")
        if self._write_engine:
            await self._write_engine.dispose()
            log.info("pg_engine.write_disposed")

    async def healthcheck(self) -> bool:
        """Verify that the read engine can connect. Returns True if healthy."""
        try:
            async with self.read_engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            return True
        except Exception as exc:
            log.error("pg_engine.healthcheck_failed", error=str(exc))
            return False


# avoid circular import at module level
import sqlalchemy as sa
