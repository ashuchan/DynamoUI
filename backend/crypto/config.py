"""Crypto subsystem settings — ``DYNAMO_CRYPTO_*`` env vars."""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CryptoSettings(BaseSettings):
    """Configuration for the AES-GCM envelope helper."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_CRYPTO_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 32 raw bytes encoded as base64. Generated via:
    #   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
    master_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Base64-encoded 32-byte AES-256 master key (KEK). MUST be set in "
            "production. An empty value disables encryption-backed features."
        ),
    )
    key_version: int = Field(
        1,
        description=(
            "Schema version for envelope payloads. Bump this when rotating to "
            "a new master key so old payloads can still be decrypted via the "
            "key history table (added in a future phase)."
        ),
    )


crypto_settings = CryptoSettings()
