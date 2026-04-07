"""Bootstrap helpers — register every cloud adapter's tester + scaffolder.

Called from ``backend/main.py`` during startup, after the connection +
scaffold services exist on ``app.state``. Keeps the wiring out of
``main.py`` so adding a new adapter only touches this file.
"""
from __future__ import annotations

from backend.adapters.cosmosdb import make_cosmosdb_tester
from backend.adapters.dynamodb import make_dynamodb_tester
from backend.adapters.dynamodb.scaffolder import DynamoDBScaffolder
from backend.adapters.kinds import COSMOSDB, DYNAMODB, ORACLE, SPANNER
from backend.adapters.oracle import make_oracle_tester
from backend.adapters.spanner import make_spanner_tester
from backend.tenants.connections.service import ConnectionService
from backend.tenants.scaffold.service import ScaffoldService


def register_cloud_adapters(
    *,
    connection_service: ConnectionService,
    scaffold_service: ScaffoldService,
) -> None:
    """Register testers + scaffolders for every Phase 5 cloud adapter."""
    connection_service.register_tester(DYNAMODB, make_dynamodb_tester(None))
    connection_service.register_tester(SPANNER, make_spanner_tester(None))
    connection_service.register_tester(ORACLE, make_oracle_tester(None))
    connection_service.register_tester(COSMOSDB, make_cosmosdb_tester(None))

    scaffold_service.register_scaffolder(DYNAMODB, DynamoDBScaffolder())
