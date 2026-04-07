"""Shared scaffolding for cloud DataAdapter implementations.

Cloud adapters typically don't share much code with the relational
``PostgreSQLAdapter`` — the query / mutation surface area is very different
(item-level KV access for DynamoDB, RPC for Spanner, etc.). This module
gives them a common base that:

* Implements the lazy-import + friendly install-hint pattern.
* Implements stubs for ``execute_query`` / ``preview_mutation`` /
  ``execute_mutation`` that raise ``NotImplementedError`` with adapter-
  specific messages until the kind is fully built out.
* Provides a ``ConnectionTester`` Protocol matching the signature
  ``ConnectionService.register_tester`` expects.

Phase 5 ships fully-functional ``connection_test`` and ``scaffold`` paths
for each cloud kind. Query/mutation execution remains stubbed and is
tracked as future work — the goal of Phase 5 is "tenants can register and
test cloud connections", not "DynamoUI can run NL queries against
DynamoDB". The adapters fail loudly so callers don't silently get back
empty results.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Awaitable, Callable

from backend.adapters.base import (
    DataAdapter,
    MutationPlan,
    QueryPlan,
    QueryResult,
)


class CloudAdapterImportError(RuntimeError):
    """Raised when a cloud SDK isn't installed.

    The message includes the exact ``pip install`` line so the operator
    can resolve it without grepping the source.
    """


def lazy_import(module_path: str, install_hint: str) -> Any:
    """Import a cloud SDK on demand, raising a helpful error if missing."""
    try:
        return __import__(module_path, fromlist=["_"])
    except ImportError as exc:
        raise CloudAdapterImportError(
            f"{module_path!r} is not installed — run: {install_hint}"
        ) from exc


class CloudDataAdapter(DataAdapter):
    """Base class for read-only cloud adapters used in Phase 5."""

    @property
    @abstractmethod
    def adapter_key(self) -> str: ...

    async def execute_query(self, skill: Any, plan: QueryPlan) -> QueryResult:
        raise NotImplementedError(
            f"{self.adapter_key!r} adapter: execute_query is not implemented yet "
            f"(Phase 5 ships connection-test + scaffold only)."
        )

    async def fetch_single(self, skill: Any, pk_value: str) -> dict[str, Any] | None:
        raise NotImplementedError(
            f"{self.adapter_key!r} adapter: fetch_single is not implemented yet."
        )

    async def preview_mutation(self, skill: Any, plan: MutationPlan) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.adapter_key!r} adapter: preview_mutation is not implemented yet."
        )

    async def execute_mutation(self, skill: Any, plan: MutationPlan) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.adapter_key!r} adapter: execute_mutation is not implemented yet."
        )

    async def validate_schema(self, skill: Any) -> None:
        raise NotImplementedError(
            f"{self.adapter_key!r} adapter: validate_schema is not implemented yet."
        )


# Type alias used by every per-adapter ``connection_test`` function.
# Matches the shape ConnectionService.register_tester expects.
ConnectionTesterFn = Callable[[dict[str, Any]], Awaitable[str | None]]
