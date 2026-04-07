"""Canonical adapter-kind identifiers.

Each cloud / RDBMS adapter registers exactly one kind here. The string is
used in three places:

* ``tenant_db_connections.adapter_kind`` (DB column)
* ``ConnectionService.register_tester(kind, ...)`` for connectivity tests
* ``ScaffoldService.register_scaffolder(kind, ...)`` for schema scaffolding

Adding a new kind: add the constant, register a tester + scaffolder in the
adapter's package, then list the kind in
``backend/adapters/__init__.register_cloud_adapters``.
"""
from __future__ import annotations

POSTGRESQL = "postgresql"
MYSQL = "mysql"
ORACLE = "oracle"
DYNAMODB = "dynamodb"
SPANNER = "spanner"
COSMOSDB = "cosmosdb"
BIGQUERY = "bigquery"
REDSHIFT = "redshift"
SNOWFLAKE = "snowflake"


ALL_KINDS = (
    POSTGRESQL,
    MYSQL,
    ORACLE,
    DYNAMODB,
    SPANNER,
    COSMOSDB,
    BIGQUERY,
    REDSHIFT,
    SNOWFLAKE,
)
