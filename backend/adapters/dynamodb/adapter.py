"""DynamoDB ``DataAdapter`` + tester + scaffolder.

The ``DynamoDBConnectionTester`` accepts a client factory so tests can
inject a fake without monkey-patching ``boto3``. In production, the factory
defaults to ``boto3.client('dynamodb', ...)``.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from backend.adapters.cloud_base import (
    CloudAdapterImportError,
    CloudDataAdapter,
    ConnectionTesterFn,
    lazy_import,
)
from backend.adapters.kinds import DYNAMODB

log = structlog.get_logger(__name__)


class DynamoDBAdapter(CloudDataAdapter):
    @property
    def adapter_key(self) -> str:
        return DYNAMODB


# A factory takes a connection dict and returns an object exposing
# ``list_tables()`` (mirrors the boto3 client surface we touch).
DynamoDBClientFactory = Callable[[dict[str, Any]], Any]


class DynamoDBConnectionTester:
    def __init__(self, client_factory: DynamoDBClientFactory | None = None) -> None:
        self._factory = client_factory or _default_factory

    async def __call__(self, connection: dict[str, Any]) -> str | None:
        try:
            client = self._factory(connection)
        except CloudAdapterImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"failed to create dynamodb client: {exc}"

        try:
            # ``list_tables`` is the cheapest read available on the service
            # and requires only ``dynamodb:ListTables``. We don't actually
            # care about the results — only that the call succeeds.
            client.list_tables(Limit=1)
        except Exception as exc:  # noqa: BLE001
            return f"dynamodb list_tables failed: {exc}"
        return None


make_dynamodb_tester: Callable[[DynamoDBClientFactory | None], ConnectionTesterFn] = (
    DynamoDBConnectionTester
)


def _default_factory(connection: dict[str, Any]) -> Any:
    boto3 = lazy_import("boto3", "pip install boto3")
    options = connection.get("options") or {}
    region = options.get("region") or options.get("aws_region")
    if not region:
        raise ValueError("dynamodb connection requires options.region")
    kwargs: dict[str, Any] = {"region_name": region}
    if connection.get("username"):
        kwargs["aws_access_key_id"] = connection["username"]
    if connection.get("password"):
        kwargs["aws_secret_access_key"] = connection["password"]
    if options.get("endpoint_url"):
        kwargs["endpoint_url"] = options["endpoint_url"]
    return boto3.client("dynamodb", **kwargs)
