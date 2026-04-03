"""
Unit tests for Pydantic Settings classes.
Verifies env var parsing, secret handling, URL construction, and validators.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


class TestSkillRegistrySettings:
    def test_defaults(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        s = SkillRegistrySettings()
        assert s.skills_dir == "./skills"
        assert s.enums_dir == "./enums"
        assert s.rest_port == 8001
        assert s.log_level == "INFO"
        assert s.log_format == "json"
        assert s.enable_slack_notifications is False
        assert s.enable_webhook_notifications is False

    def test_env_override(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        with patch.dict(os.environ, {"DYNAMO_SKILL_REST_PORT": "9000"}):
            s = SkillRegistrySettings()
            assert s.rest_port == 9000

    def test_jwt_secret_is_secret_str(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        from pydantic import SecretStr
        s = SkillRegistrySettings()
        assert isinstance(s.jwt_secret, SecretStr)
        # Must not appear as plaintext in repr
        assert "dev-insecure-change-me" not in repr(s)

    def test_invalid_log_level_raises(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        with patch.dict(os.environ, {"DYNAMO_SKILL_LOG_LEVEL": "VERBOSE"}):
            with pytest.raises(ValidationError):
                SkillRegistrySettings()

    def test_shadow_threshold_out_of_range_raises(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        with patch.dict(os.environ, {"DYNAMO_SKILL_FUZZY_MATCH_SHADOW_THRESHOLD": "1.5"}):
            with pytest.raises(ValidationError):
                SkillRegistrySettings()

    def test_shadow_threshold_at_boundary(self):
        from backend.skill_registry.config.settings import SkillRegistrySettings
        with patch.dict(os.environ, {"DYNAMO_SKILL_FUZZY_MATCH_SHADOW_THRESHOLD": "1.0"}):
            s = SkillRegistrySettings()
            assert s.fuzzy_match_shadow_threshold == 1.0


class TestPostgreSQLSettings:
    def test_defaults(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        s = PostgreSQLSettings()
        assert s.host == "localhost"
        assert s.port == 5432
        assert s.database == "dynamoui"
        assert s.pool_size == 10
        assert s.ssl_mode == "prefer"

    def test_read_url_construction(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        s = PostgreSQLSettings()
        url = s.read_url
        assert "postgresql+asyncpg://" in url
        assert s.user in url
        assert s.host in url
        assert s.database in url

    def test_write_url_uses_write_user(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        s = PostgreSQLSettings()
        assert s.write_user in s.write_url

    def test_password_is_secret_str(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        s = PostgreSQLSettings()
        from pydantic import SecretStr
        assert isinstance(s.password, SecretStr)

    def test_password_not_in_url_repr(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        import os
        with patch.dict(os.environ, {"DYNAMO_PG_PASSWORD": "supersecret123"}):
            s = PostgreSQLSettings()
            # URL is built via get_secret_value() at build time — not stored in repr
            assert "supersecret123" not in repr(s)

    def test_invalid_ssl_mode_raises(self):
        from backend.skill_registry.config.settings import PostgreSQLSettings
        with patch.dict(os.environ, {"DYNAMO_PG_SSL_MODE": "invalid_mode"}):
            with pytest.raises(ValidationError):
                PostgreSQLSettings()


class TestPatternCacheSettings:
    def test_defaults(self):
        from backend.skill_registry.config.settings import PatternCacheSettings
        s = PatternCacheSettings()
        assert s.fuzzy_threshold == 0.90
        assert s.auto_promote_enabled is True  # enabled now that Phase 2 promotion is implemented
        assert s.enforce_skill_hash is True
        assert s.hash_length == 16

    def test_auto_promote_enabled_by_default(self):
        """Phase 2 promotion is now implemented — auto_promote_enabled defaults to True."""
        from backend.skill_registry.config.settings import PatternCacheSettings
        s = PatternCacheSettings()
        assert s.auto_promote_enabled is True

    def test_fuzzy_threshold_out_of_range_raises(self):
        from backend.skill_registry.config.settings import PatternCacheSettings
        with patch.dict(os.environ, {"DYNAMO_CACHE_FUZZY_THRESHOLD": "1.1"}):
            with pytest.raises(ValidationError):
                PatternCacheSettings()

    def test_stopwords_default_list(self):
        from backend.skill_registry.config.settings import PatternCacheSettings
        s = PatternCacheSettings()
        assert "show" in s.stopwords
        assert "the" in s.stopwords
        assert "find" in s.stopwords
