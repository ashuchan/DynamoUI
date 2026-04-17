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


class VerifierSettings(BaseSettings):
    """DYNAMO_VERIFIER_* environment variables — controls the LLM verification loop.

    The whole loop is gated by ``enabled``. When False (default), /resolve
    behaves exactly as it did pre-v2: pattern cache → LLM synth, no verifier.
    """

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_VERIFIER_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        False,
        description="Master toggle for the LLM verification loop. Default off.",
    )
    verify_cache_hits: bool = Field(True)
    verify_synthesised: bool = Field(False)
    verify_templates: bool = Field(True)
    verify_saved_views: bool = Field(False)
    skip_on_confidence_above: float = Field(0.98)
    skip_intents: list[str] = Field(default_factory=lambda: ["NAVIGATE"])
    llm_timeout_ms: int = Field(1500)
    on_llm_failure: Literal["approve_candidate", "reject_and_synth"] = Field(
        "approve_candidate",
        description="Graceful degradation when the verifier LLM call fails or times out.",
    )
    verifier_model: str = Field(
        "",
        description="Override model for verifier. Empty → share the primary LLM model.",
    )
    verdict_cache_size: int = Field(2048, description="LRU size for verdict cache.")
    monthly_budget_usd: float = Field(
        0.0,
        description="Per-tenant monthly budget. 0 = unlimited. Enforced by circuit breaker.",
    )
    parallel_execution: bool = Field(
        True,
        description="Start candidate execution in parallel with verification (cache-hit path).",
    )


class FeatureFlagSettings(BaseSettings):
    """DYNAMO_FEATURE_* environment variables — cluster on/off switches for v2."""

    model_config = SettingsConfigDict(
        env_prefix="DYNAMO_FEATURE_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    personalisation: bool = Field(True, description="M2 — saved views + dashboards")
    provenance: bool = Field(True, description="M4 — provenance envelope on responses")
    scheduling: bool = Field(True, description="M5 — scheduled delivery")
    alerts: bool = Field(True, description="M6 — threshold alerts")
    palette: bool = Field(True, description="M7 — command palette + slash commands")
    sharing: bool = Field(True, description="M8 — shareable links + embed tokens")
    expose_sql: bool = Field(
        False,
        description="Include generated SQL in the provenance envelope. Default off in prod.",
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
verifier_settings = VerifierSettings()
feature_settings = FeatureFlagSettings()


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
