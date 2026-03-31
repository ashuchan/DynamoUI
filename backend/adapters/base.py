"""
DataAdapter ABC + data classes for query and mutation plans.
All adapters must implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FilterClause:
    """A single filter predicate."""

    field: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "is_null"]
    value: Any


@dataclass
class SortClause:
    """A sort directive."""

    field: str
    dir: Literal["asc", "desc"] = "asc"


@dataclass
class QueryPlan:
    """
    Describes a read operation against an entity.
    Built by the pattern cache or LLM resolver; executed by the adapter.
    """

    entity: str
    filters: list[FilterClause] = field(default_factory=list)
    sort: list[SortClause] = field(default_factory=list)
    page: int = 1
    page_size: int = 25
    select_fields: list[str] = field(default_factory=list)


@dataclass
class QueryResult:
    """Result of executing a QueryPlan."""

    rows: list[dict[str, Any]]
    total_count: int
    page: int
    page_size: int


@dataclass
class MutationPlan:
    """
    Describes a write operation against an entity.
    Always requires a diff preview before execution.
    """

    entity: str
    mutation_id: str
    operation: Literal["create", "update", "delete"]
    record_pk: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class MutationResult:
    """Result of executing a MutationPlan."""

    success: bool
    affected_pk: str | None = None
    error: str | None = None
    audit_log_id: str | None = None


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class DataAdapter(ABC):
    """
    Abstract base class for all DynamoUI data adapters.
    New adapters: implement all abstract methods + register in adapters.registry.yaml.
    Phase 1 only requires PostgreSQLAdapter.
    """

    @property
    @abstractmethod
    def adapter_key(self) -> str:
        """Short identifier matching adapters.registry.yaml key."""

    @abstractmethod
    async def execute_query(
        self,
        skill: Any,
        plan: QueryPlan,
    ) -> QueryResult:
        """Execute a read QueryPlan and return rows + total count."""

    @abstractmethod
    async def fetch_single(
        self,
        skill: Any,
        pk_value: str,
    ) -> dict[str, Any] | None:
        """Fetch a single record by PK. Returns None if not found."""

    @abstractmethod
    async def preview_mutation(
        self,
        skill: Any,
        plan: MutationPlan,
    ) -> dict[str, Any]:
        """
        Build a diff preview in memory. MUST NOT write to the database.
        Returns a dict suitable for the /mutate/preview response.
        """

    @abstractmethod
    async def execute_mutation(
        self,
        skill: Any,
        plan: MutationPlan,
    ) -> dict[str, Any]:
        """
        Execute a confirmed mutation within a transaction.
        Automatic rollback on failure.
        """

    @abstractmethod
    async def validate_schema(self, skill: Any) -> None:
        """
        Phase 4: validate that the skill YAML matches the live DB schema.
        Raises ValueError with details on mismatch.
        """

    def scaffold(self, *args, **kwargs) -> Any:
        """
        Generate skill YAML from a live DB schema.
        Adapters that cannot support scaffold must raise NotImplementedError.
        """
        raise NotImplementedError(
            f"Adapter {self.adapter_key!r} does not support schema scaffolding"
        )
