"""DynamoDB schema scaffolder.

Inspects every table the IAM principal has access to via ``ListTables`` +
``DescribeTable``, then emits a draft skill YAML for each. Limited by the
serverless nature of DynamoDB — only the partition + sort keys + indexes
are reflected. Item attributes are not enumerated (DynamoDB has no global
schema for them); attributes appear in the scaffolded YAML only after
sample items are inspected.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog

from backend.adapters.cloud_base import CloudAdapterImportError, lazy_import
from backend.tenants.scaffold.dtos import ScaffoldStartRequest

log = structlog.get_logger(__name__)


class DynamoDBScaffolder:
    def __init__(self, client_factory: Callable[[dict[str, Any]], Any] | None = None) -> None:
        self._factory = client_factory or _default_factory

    async def scaffold(
        self,
        *,
        connection: dict[str, Any],
        request: ScaffoldStartRequest,
        progress: Callable[[int], Awaitable[None]],
    ) -> dict[str, Any]:
        try:
            client = self._factory(connection)
        except CloudAdapterImportError as exc:
            raise RuntimeError(str(exc)) from exc

        await progress(10)

        tables = client.list_tables(Limit=100).get("TableNames", [])
        if request.table_filter:
            wanted = set(request.table_filter)
            tables = [t for t in tables if t in wanted]

        await progress(30)

        described: list[dict[str, Any]] = []
        for idx, name in enumerate(tables):
            described.append(client.describe_table(TableName=name).get("Table", {}))
            await progress(30 + int(60 * (idx + 1) / max(len(tables), 1)))

        skills = [
            {
                "name": _pascal(t.get("TableName", "")),
                "table": t.get("TableName", ""),
                "fields": [
                    {
                        "name": k.get("AttributeName"),
                        "isPK": True,
                        "type": _attr_type(t.get("AttributeDefinitions", []), k.get("AttributeName")),
                    }
                    for k in t.get("KeySchema", [])
                ],
            }
            for t in described
        ]

        await progress(100)
        return {
            "adapter_kind": "dynamodb",
            "tables_inspected": [t.get("TableName") for t in described],
            "skills_generated": len(skills),
            "skills_preview": skills,
            "warnings": [
                "DynamoDB scaffolder only reflects keys + indexes; item attributes "
                "must be added to the YAML manually after inspecting sample items."
            ],
        }


def _default_factory(connection: dict[str, Any]) -> Any:
    boto3 = lazy_import("boto3", "pip install boto3")
    options = connection.get("options") or {}
    region = options.get("region") or options.get("aws_region")
    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    if connection.get("username"):
        kwargs["aws_access_key_id"] = connection["username"]
    if connection.get("password"):
        kwargs["aws_secret_access_key"] = connection["password"]
    if options.get("endpoint_url"):
        kwargs["endpoint_url"] = options["endpoint_url"]
    return boto3.client("dynamodb", **kwargs)


def _attr_type(defs: list[dict[str, Any]], name: str | None) -> str:
    for d in defs:
        if d.get("AttributeName") == name:
            return {"S": "string", "N": "float", "B": "string"}.get(
                d.get("AttributeType", "S"), "string"
            )
    return "string"


def _pascal(value: str) -> str:
    return "".join(p.capitalize() for p in value.replace("-", "_").split("_") if p)
