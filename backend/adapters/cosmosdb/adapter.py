"""Azure Cosmos DB adapter (SQL API)."""
from __future__ import annotations

from typing import Any, Callable

from backend.adapters.cloud_base import (
    CloudAdapterImportError,
    CloudDataAdapter,
    ConnectionTesterFn,
    lazy_import,
)
from backend.adapters.kinds import COSMOSDB


class CosmosDBAdapter(CloudDataAdapter):
    @property
    def adapter_key(self) -> str:
        return COSMOSDB


CosmosClientFactory = Callable[[dict[str, Any]], Any]


class CosmosDBConnectionTester:
    """Verifies a Cosmos connection by listing databases."""

    def __init__(self, client_factory: CosmosClientFactory | None = None) -> None:
        self._factory = client_factory or _default_factory

    async def __call__(self, connection: dict[str, Any]) -> str | None:
        try:
            client = self._factory(connection)
        except CloudAdapterImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"failed to obtain cosmos client: {exc}"

        try:
            list(client.list_databases())
        except Exception as exc:  # noqa: BLE001
            return f"cosmos list_databases failed: {exc}"
        return None


make_cosmosdb_tester: Callable[[CosmosClientFactory | None], ConnectionTesterFn] = (
    CosmosDBConnectionTester
)


def _default_factory(connection: dict[str, Any]) -> Any:
    cosmos = lazy_import("azure.cosmos", "pip install azure-cosmos")
    options = connection.get("options") or {}
    endpoint = options.get("endpoint") or connection.get("host")
    key = connection.get("password") or options.get("key")
    if not endpoint or not key:
        raise ValueError(
            "cosmos connection requires options.endpoint (or host) + password (key)"
        )
    return cosmos.CosmosClient(url=endpoint, credential=key)
