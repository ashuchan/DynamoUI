"""
AdapterRegistry — runtime registry of DataAdapter instances.
Reads adapters.registry.yaml at startup; maps keys to live adapter objects.
"""
from __future__ import annotations

import structlog

from backend.adapters.base import DataAdapter

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_registry: dict[str, DataAdapter] = {}


def register_adapter(adapter: DataAdapter) -> None:
    """Register a DataAdapter instance under its adapter_key."""
    log.info("adapter_registry.registered", key=adapter.adapter_key)
    _registry[adapter.adapter_key] = adapter


def get_adapter(key: str) -> DataAdapter | None:
    return _registry.get(key)


def get_adapter_registry() -> _AdapterRegistryProxy:
    return _AdapterRegistryProxy()


class _AdapterRegistryProxy:
    """Thin proxy over the module-level dict for cleaner API usage."""

    def get(self, key: str) -> DataAdapter | None:
        return _registry.get(key)

    def keys(self) -> list[str]:
        return list(_registry.keys())

    def all(self) -> list[DataAdapter]:
        return list(_registry.values())


async def initialise_adapters(
    adapter_registry_yaml_path: str,
    pg_settings: object | None = None,
) -> None:
    """
    Instantiate and register all adapters described in adapters.registry.yaml.
    Called once during startup before serving requests.
    """
    from pathlib import Path

    from backend.skill_registry.loader.yaml_loader import load_adapter_registry

    path = Path(adapter_registry_yaml_path)
    if not path.exists():
        log.warning("adapter_registry.file_missing", path=str(path))
        return

    adapter_reg = load_adapter_registry(path)

    for entry in adapter_reg.adapters:
        if entry.type == "postgresql":
            from backend.adapters.postgresql.adapter import PostgreSQLAdapter

            adapter = PostgreSQLAdapter(adapter_key=entry.key, settings=pg_settings)
            register_adapter(adapter)
        else:
            log.warning(
                "adapter_registry.unknown_type",
                key=entry.key,
                type=entry.type,
            )

    log.info(
        "adapter_registry.initialised",
        count=len(_registry),
        keys=list(_registry.keys()),
    )
