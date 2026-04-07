"""Oracle adapter built on the ``oracledb`` thin driver."""
from __future__ import annotations

from typing import Any, Callable

from backend.adapters.cloud_base import (
    CloudAdapterImportError,
    CloudDataAdapter,
    ConnectionTesterFn,
    lazy_import,
)
from backend.adapters.kinds import ORACLE


class OracleAdapter(CloudDataAdapter):
    @property
    def adapter_key(self) -> str:
        return ORACLE


OracleConnectionFactory = Callable[[dict[str, Any]], Any]


class OracleConnectionTester:
    """Opens a connection, runs ``SELECT 1 FROM DUAL`` and closes."""

    def __init__(self, connection_factory: OracleConnectionFactory | None = None) -> None:
        self._factory = connection_factory or _default_factory

    async def __call__(self, connection: dict[str, Any]) -> str | None:
        try:
            conn = self._factory(connection)
        except CloudAdapterImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"failed to open oracle connection: {exc}"

        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT 1 FROM DUAL")
                cursor.fetchone()
            finally:
                cursor.close()
        except Exception as exc:  # noqa: BLE001
            try:
                conn.close()
            except Exception:
                pass
            return f"oracle test query failed: {exc}"
        try:
            conn.close()
        except Exception:
            pass
        return None


make_oracle_tester: Callable[[OracleConnectionFactory | None], ConnectionTesterFn] = (
    OracleConnectionTester
)


def _default_factory(connection: dict[str, Any]) -> Any:
    oracledb = lazy_import("oracledb", "pip install oracledb")
    options = connection.get("options") or {}
    dsn = options.get("dsn")
    if not dsn:
        host = connection.get("host")
        port = connection.get("port") or 1521
        service = connection.get("database") or options.get("service_name")
        if not host or not service:
            raise ValueError(
                "oracle connection requires either options.dsn or host + database"
            )
        dsn = f"{host}:{port}/{service}"
    return oracledb.connect(
        user=connection.get("username"),
        password=connection.get("password"),
        dsn=dsn,
    )
