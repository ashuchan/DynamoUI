"""Azure Cosmos DB adapter (Phase 5)."""
from backend.adapters.cosmosdb.adapter import (
    CosmosDBAdapter,
    CosmosDBConnectionTester,
    make_cosmosdb_tester,
)

__all__ = ["CosmosDBAdapter", "CosmosDBConnectionTester", "make_cosmosdb_tester"]
