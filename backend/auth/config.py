"""Auth subsystem settings — ``DYNAMO_AUTH_*`` env vars."""
from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Settings for the auth subsystem."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_AUTH_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-insecure-auth-secret-change-me"),
        description="HS256 signing secret for access tokens. MUST be set in prod.",
    )
    jwt_algorithm: str = Field("HS256")
    access_token_ttl_seconds: int = Field(
        3600, description="Access token lifetime (default 1h)."
    )
    signup_enabled: bool = Field(
        True, description="Disable to shut off public signups."
    )

    google_client_id: str = Field(
        "",
        description="Google OAuth client id. Leave empty to disable Google login.",
    )
    google_tokeninfo_url: str = Field(
        "https://oauth2.googleapis.com/tokeninfo",
        description="Google token verification endpoint (override in tests).",
    )

    # Password hashing — stdlib scrypt parameters
    scrypt_n: int = Field(2**14, description="scrypt CPU/memory cost factor")
    scrypt_r: int = Field(8, description="scrypt block size")
    scrypt_p: int = Field(1, description="scrypt parallelisation")
    scrypt_dklen: int = Field(32, description="derived key length in bytes")


auth_settings = AuthSettings()
