"""Cloud Spanner adapter."""
from __future__ import annotations

from typing import Any, Callable

from backend.adapters.cloud_base import (
    CloudAdapterImportError,
    CloudDataAdapter,
    ConnectionTesterFn,
    lazy_import,
)
from backend.adapters.kinds import SPANNER


class SpannerAdapter(CloudDataAdapter):
    @property
    def adapter_key(self) -> str:
        return SPANNER


SpannerClientFactory = Callable[[dict[str, Any]], Any]


class SpannerConnectionTester:
    """Verifies a Spanner connection by listing databases on the instance."""

    def __init__(self, client_factory: SpannerClientFactory | None = None) -> None:
        self._factory = client_factory or _default_factory

    async def __call__(self, connection: dict[str, Any]) -> str | None:
        try:
            instance = self._factory(connection)
        except CloudAdapterImportError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"failed to obtain spanner instance handle: {exc}"

        try:
            list(instance.list_databases())
        except Exception as exc:  # noqa: BLE001
            return f"spanner list_databases failed: {exc}"
        return None


make_spanner_tester: Callable[[SpannerClientFactory | None], ConnectionTesterFn] = (
    SpannerConnectionTester
)


def _default_factory(connection: dict[str, Any]) -> Any:
    spanner = lazy_import(
        "google.cloud.spanner", "pip install google-cloud-spanner"
    )
    options = connection.get("options") or {}
    project = options.get("project") or options.get("gcp_project")
    instance_id = options.get("instance_id") or connection.get("database")
    if not project or not instance_id:
        raise ValueError(
            "spanner connection requires options.project and options.instance_id"
        )
    client = spanner.Client(project=project)
    return client.instance(instance_id)
