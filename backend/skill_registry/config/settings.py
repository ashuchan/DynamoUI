"""
Pydantic Settings for the Skill Registry and PostgreSQL adapter.
All secrets use SecretStr — never logged or serialised as plaintext.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SkillRegistrySettings(BaseSettings):
    """DYNAMO_SKILL_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="DYNAMO_SKILL_", case_sensitive=False)

    skills_dir: str = Field("./skills", description="*.skill.yaml discovery root")
    enums_dir: str = Field("./enums", description="*.enum.yaml discovery root")
    adapters_registry: str = Field(
        "./adapters.registry.yaml", description="Path to adapter registry YAML"
    )
    rest_port: int = Field(8001, description="FastAPI listen port")
    jwt_secret: SecretStr = Field(
        default=SecretStr("dev-insecure-change-me"),
        description="JWT signing secret. Must be set in production.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO")
    log_format: Literal["json", "console"] = Field("json")
    enable_slack_notifications: bool = Field(
        False,
        description="Feature-flagged off. Phase 2 only.",
    )
    enable_webhook_notifications: bool = Field(
        False,
        description="Feature-flagged off. Phase 2 only.",
    )
    fuzzy_match_shadow_threshold: float = Field(
        0.85,
        description="Similarity threshold for shadowed-trigger detection (0.0–1.0)",
    )

    @field_validator("fuzzy_match_shadow_threshold")
    @classmethod
    def _validate_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("fuzzy_match_shadow_threshold must be between 0.0 and 1.0")
        return v


class PostgreSQLSettings(BaseSettings):
    """DYNAMO_PG_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="DYNAMO_PG_", case_sensitive=False)

    host: str = Field("localhost")
    port: int = Field(5432)
    database: str = Field("dynamoui")
    user: str = Field("dynamoui_reader", description="Read-only user for all queries")
    password: SecretStr = Field(
        default=SecretStr(""),
        description="Never hardcode. Must be provided via env in production.",
    )
    write_user: str = Field("dynamoui_writer", description="Mutations only")
    write_password: SecretStr = Field(
        default=SecretStr(""),
        description="Never hardcode. Must be provided via env in production.",
    )
    pool_size: int = Field(10)
    max_overflow: int = Field(20)
    pool_timeout: int = Field(30, description="Seconds")
    pool_recycle: int = Field(3600, description="Seconds")
    echo_sql: bool = Field(False, description="Dev only — logs all SQL")
    ssl_mode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = Field(
        "prefer", description="Use 'require' in production"
    )

    @property
    def read_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:"
            f"{self.password.get_secret_value()}@{self.host}:{self.port}/{self.database}"
        )

    @property
    def write_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.write_user}:"
            f"{self.write_password.get_secret_value()}@{self.host}:{self.port}/{self.database}"
        )


class PatternCacheSettings(BaseSettings):
    """DYNAMO_CACHE_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="DYNAMO_CACHE_", case_sensitive=False)

    fuzzy_threshold: float = Field(
        0.90, description="Minimum score for a cache hit (0.0–1.0)"
    )
    fuzzy_scorer: str = Field("token_sort_ratio")
    entity_scoped_matching: bool = Field(
        True, description="Prefer entity-scoped trigger lookup"
    )
    stopwords: list[str] = Field(
        default_factory=lambda: [
            "the", "a", "an", "all", "show", "me", "get",
            "find", "list", "please", "can", "you",
        ]
    )
    auto_promote_enabled: bool = Field(
        False,
        description="Phase 2 stub — must remain false in Phase 1",
    )
    enforce_skill_hash: bool = Field(
        True, description="Reject patterns with stale skill hashes"
    )
    hash_length: int = Field(16, description="Truncated SHA-256 prefix length")
    stats_log_interval_seconds: int = Field(
        300, description="Hit rate log interval in seconds"
    )

    @field_validator("fuzzy_threshold")
    @classmethod
    def _validate_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("fuzzy_threshold must be between 0.0 and 1.0")
        return v


# ---------------------------------------------------------------------------
# Module-level singletons — constructed once at import time.
# Tests can override by monkey-patching or using dependency injection.
# ---------------------------------------------------------------------------
skill_settings = SkillRegistrySettings()
pg_settings = PostgreSQLSettings()
cache_settings = PatternCacheSettings()


def configure_logging(settings: SkillRegistrySettings = skill_settings) -> None:
    """Configure structlog based on settings. Call once at startup."""
    import structlog

    log_level = getattr(logging, settings.log_level)
    logging.basicConfig(level=log_level)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
