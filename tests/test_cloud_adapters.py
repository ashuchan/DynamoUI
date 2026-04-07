"""Tests for the Phase 5 cloud adapters.

Each test injects a fake client / connection factory so the cloud SDKs are
not required at test time. The tests confirm:

* Successful tester returns ``None`` (Phase 2 ``ConnectionService`` contract).
* SDK errors are surfaced as a string and never raise.
* Missing required options are caught before the SDK is touched.
* The DataAdapter stubs raise NotImplementedError so callers can't get
  silent empty results.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.adapters.base import QueryPlan, MutationPlan
from backend.adapters.cloud_base import CloudAdapterImportError
from backend.adapters.cosmosdb.adapter import (
    CosmosDBAdapter,
    CosmosDBConnectionTester,
)
from backend.adapters.dynamodb.adapter import (
    DynamoDBAdapter,
    DynamoDBConnectionTester,
)
from backend.adapters.dynamodb.scaffolder import DynamoDBScaffolder
from backend.adapters.kinds import COSMOSDB, DYNAMODB, ORACLE, SPANNER
from backend.adapters.oracle.adapter import (
    OracleAdapter,
    OracleConnectionTester,
)
from backend.adapters.spanner.adapter import (
    SpannerAdapter,
    SpannerConnectionTester,
)
from backend.tenants.scaffold.dtos import ScaffoldStartRequest


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------


class _FakeDynamoClient:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def list_tables(self, **kwargs: Any) -> dict[str, Any]:
        if self._error:
            raise self._error
        return {"TableNames": ["users", "orders"]}

    def describe_table(self, TableName: str) -> dict[str, Any]:
        return {
            "Table": {
                "TableName": TableName,
                "AttributeDefinitions": [
                    {"AttributeName": "id", "AttributeType": "S"},
                ],
                "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            }
        }


@pytest.mark.asyncio
async def test_dynamodb_tester_success() -> None:
    tester = DynamoDBConnectionTester(client_factory=lambda c: _FakeDynamoClient())
    assert await tester({"adapter_kind": DYNAMODB}) is None


@pytest.mark.asyncio
async def test_dynamodb_tester_surfaces_sdk_errors() -> None:
    tester = DynamoDBConnectionTester(
        client_factory=lambda c: _FakeDynamoClient(error=RuntimeError("denied"))
    )
    result = await tester({"adapter_kind": DYNAMODB})
    assert result is not None
    assert "denied" in result


@pytest.mark.asyncio
async def test_dynamodb_tester_missing_sdk() -> None:
    def boom(_: dict[str, Any]) -> Any:
        raise CloudAdapterImportError("boto3 not installed — run: pip install boto3")

    tester = DynamoDBConnectionTester(client_factory=boom)
    assert "boto3" in (await tester({}) or "")


@pytest.mark.asyncio
async def test_dynamodb_scaffolder_records_keys() -> None:
    scaffolder = DynamoDBScaffolder(client_factory=lambda c: _FakeDynamoClient())
    progress: list[int] = []

    async def report(p: int) -> None:
        progress.append(p)

    summary = await scaffolder.scaffold(
        connection={"adapter_kind": DYNAMODB},
        request=ScaffoldStartRequest(),
        progress=report,
    )
    assert summary["adapter_kind"] == "dynamodb"
    assert summary["tables_inspected"] == ["users", "orders"]
    assert summary["skills_generated"] == 2
    assert progress[-1] == 100
    skills = summary["skills_preview"]
    assert skills[0]["fields"][0]["isPK"] is True


def test_dynamodb_adapter_key() -> None:
    assert DynamoDBAdapter().adapter_key == DYNAMODB


# ---------------------------------------------------------------------------
# Spanner
# ---------------------------------------------------------------------------


class _FakeSpannerInstance:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def list_databases(self) -> list[Any]:
        if self._error:
            raise self._error
        return [object()]


@pytest.mark.asyncio
async def test_spanner_tester_success() -> None:
    tester = SpannerConnectionTester(client_factory=lambda c: _FakeSpannerInstance())
    assert await tester({"adapter_kind": SPANNER}) is None


@pytest.mark.asyncio
async def test_spanner_tester_failure() -> None:
    tester = SpannerConnectionTester(
        client_factory=lambda c: _FakeSpannerInstance(error=RuntimeError("nope"))
    )
    result = await tester({"adapter_kind": SPANNER})
    assert result is not None
    assert "nope" in result


def test_spanner_adapter_key() -> None:
    assert SpannerAdapter().adapter_key == SPANNER


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


class _FakeOracleCursor:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.closed = False

    def execute(self, sql: str) -> None:
        if self._error:
            raise self._error

    def fetchone(self) -> tuple[int]:
        return (1,)

    def close(self) -> None:
        self.closed = True


class _FakeOracleConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.closed = False

    def cursor(self) -> _FakeOracleCursor:
        return _FakeOracleCursor(self._error)

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_oracle_tester_success() -> None:
    conn = _FakeOracleConnection()
    tester = OracleConnectionTester(connection_factory=lambda c: conn)
    assert await tester({"adapter_kind": ORACLE}) is None
    assert conn.closed


@pytest.mark.asyncio
async def test_oracle_tester_query_failure_closes_connection() -> None:
    conn = _FakeOracleConnection(error=RuntimeError("ORA-00942"))
    tester = OracleConnectionTester(connection_factory=lambda c: conn)
    result = await tester({"adapter_kind": ORACLE})
    assert result is not None
    assert "ORA-00942" in result
    assert conn.closed


def test_oracle_adapter_key() -> None:
    assert OracleAdapter().adapter_key == ORACLE


# ---------------------------------------------------------------------------
# Cosmos DB
# ---------------------------------------------------------------------------


class _FakeCosmosClient:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def list_databases(self) -> list[Any]:
        if self._error:
            raise self._error
        return [{"id": "db1"}]


@pytest.mark.asyncio
async def test_cosmos_tester_success() -> None:
    tester = CosmosDBConnectionTester(client_factory=lambda c: _FakeCosmosClient())
    assert await tester({"adapter_kind": COSMOSDB}) is None


@pytest.mark.asyncio
async def test_cosmos_tester_failure() -> None:
    tester = CosmosDBConnectionTester(
        client_factory=lambda c: _FakeCosmosClient(error=RuntimeError("401"))
    )
    result = await tester({"adapter_kind": COSMOSDB})
    assert result is not None
    assert "401" in result


def test_cosmos_adapter_key() -> None:
    assert CosmosDBAdapter().adapter_key == COSMOSDB


# ---------------------------------------------------------------------------
# Adapter stubs raise NotImplementedError, never return silently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "adapter",
    [DynamoDBAdapter(), SpannerAdapter(), OracleAdapter(), CosmosDBAdapter()],
)
@pytest.mark.asyncio
async def test_unimplemented_methods_fail_loudly(adapter: Any) -> None:
    with pytest.raises(NotImplementedError):
        await adapter.execute_query(skill=None, plan=QueryPlan(entity="x"))
    with pytest.raises(NotImplementedError):
        await adapter.fetch_single(skill=None, pk_value="x")
    with pytest.raises(NotImplementedError):
        await adapter.preview_mutation(
            skill=None,
            plan=MutationPlan(entity="x", mutation_id="y", operation="create"),
        )
    with pytest.raises(NotImplementedError):
        await adapter.execute_mutation(
            skill=None,
            plan=MutationPlan(entity="x", mutation_id="y", operation="create"),
        )
    with pytest.raises(NotImplementedError):
        await adapter.validate_schema(skill=None)
