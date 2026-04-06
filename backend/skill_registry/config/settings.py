"""
Pydantic Settings for the Skill Registry and PostgreSQL adapter.
All secrets use SecretStr — never logged or serialised as plaintext.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SkillRegistrySettings(BaseSettings):
    """DYNAMO_SKILL_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_SKILL_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_PG_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
        from urllib.parse import quote_plus
        return (
            f"postgresql+asyncpg://{quote_plus(self.user)}:"
            f"{quote_plus(self.password.get_secret_value())}@{self.host}:{self.port}/{self.database}"
        )

    @property
    def write_url(self) -> str:
        from urllib.parse import quote_plus
        return (
            f"postgresql+asyncpg://{quote_plus(self.write_user)}:"
            f"{quote_plus(self.write_password.get_secret_value())}@{self.host}:{self.port}/{self.database}"
        )


class PatternCacheSettings(BaseSettings):
    """DYNAMO_CACHE_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_CACHE_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
        True,
        description="Enable write-back of high-confidence LLM patterns to YAML files",
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


class LLMSettings(BaseSettings):
    """DYNAMO_LLM_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_LLM_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: Literal["anthropic", "google"] = Field("anthropic")
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    anthropic_model: str = Field("claude-haiku-4-5-20251001")
    google_api_key: SecretStr = Field(default=SecretStr(""))
    google_model: str = Field("gemini-1.5-flash")
    max_tokens: int = Field(8192)
    timeout_seconds: float = Field(60.0)
    auto_promote_threshold: float = Field(
        0.95,
        description="LLM synthesis confidence >= this → auto-promote to patterns YAML",
    )
    review_queue_threshold: float = Field(
        0.90,
        description="confidence >= this but < auto_promote → write to review queue",
    )
    review_queue_path: str = Field("./pattern_reviews/")
    auto_promote_enabled: bool = Field(
        True,
        description="Enable write-back of high-confidence LLM patterns to YAML files",
    )


class InternalSettings(BaseSettings):
    """
    DYNAMO_INTERNAL_* — configuration for DynamoUI-managed internal tables.
    All metering tables live in a dedicated PostgreSQL schema (default: dynamoui_internal),
    isolated from the business-data schemas managed by adapters.
    """

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_INTERNAL_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_schema: str = Field(
        "dynamoui_internal",
        description="PostgreSQL schema for all DynamoUI-managed tables",
    )
    db_url: str = Field(
        "",
        description=(
            "Full async SQLAlchemy URL for the internal schema. "
            "Defaults to the pg_settings write URL when empty."
        ),
    )

    def resolved_db_url(self, pg: "PostgreSQLSettings") -> str:
        """Return the configured URL or fall back to the write pool URL."""
        return self.db_url if self.db_url else pg.write_url


# ---------------------------------------------------------------------------
# Module-level singletons — constructed once at import time.
# Tests can override by monkey-patching or using dependency injection.
# ---------------------------------------------------------------------------
skill_settings = SkillRegistrySettings()
pg_settings = PostgreSQLSettings()
cache_settings = PatternCacheSettings()
llm_settings = LLMSettings()
internal_settings = InternalSettings()


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
