"""Settings for the tenant registry runtime cache."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TenantRegistrySettings(BaseSettings):
    """``DYNAMO_TENANT_*`` env vars."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_TENANT_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    registry_cache_size: int = Field(
        64,
        ge=1,
        description=(
            "Maximum number of TenantRegistryView instances kept in memory. "
            "Eviction is strictly LRU keyed on tenant_id."
        ),
    )


tenant_registry_settings = TenantRegistrySettings()
