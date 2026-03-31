"""
API integration tests using FastAPI TestClient.
Tests all /api/v1/* endpoints with a pre-seeded SkillRegistry and PatternCache.
No PostgreSQL connection required for schema/enum/pattern endpoints.
DB-dependent endpoints are marked @pytest.mark.integration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.skill_registry.models.registry import SkillRegistry
from backend.pattern_cache.cache.pattern_cache import PatternCache


# ---------------------------------------------------------------------------
# Test app factory — injects fixtures without touching the DB
# ---------------------------------------------------------------------------


def _make_test_app(
    employee_skill,
    employment_type_enum,
    department_enum,
    employee_pattern_file,
) -> FastAPI:
    """
    Build a minimal FastAPI app with pre-seeded state.
    Skips the startup sequence entirely — safe for unit tests.
    """
    from fastapi import FastAPI
    from backend.skill_registry.api.rest_router import router as skill_router
    from backend.pattern_cache.api.rest_router import router as patterns_router
    from backend.skill_registry.api.widgets_router import router as widgets_router

    app = FastAPI()
    prefix = "/api/v1"
    app.include_router(skill_router, prefix=prefix)
    app.include_router(patterns_router, prefix=prefix)
    app.include_router(widgets_router, prefix=prefix)

    registry = SkillRegistry()
    registry.register_entity(employee_skill)
    registry.register_enum(employment_type_enum)
    registry.register_enum(department_enum)
    registry.build_fk_graph()

    from backend.skill_registry.registry.enum_registry import EnumRegistry
    enum_reg = EnumRegistry()
    enum_reg.register_all(list(registry.enum_by_name.values()))
    registry._enum_registry = enum_reg

    cache = PatternCache(threshold=0.90, enforce_skill_hash=False)
    cache.build_from_pattern_files([employee_pattern_file])

    app.state.skill_registry = registry
    app.state.pattern_cache = cache
    app.state.widgets = {
        "widgets": [
            {
                "id": "active_employees",
                "title": "Active Employees",
                "entity": "Employee",
                "pattern_id": "employee.active",
                "category": "HR",
                "params": [],
            }
        ]
    }
    return app


@pytest.fixture
def test_client(employee_skill, employment_type_enum, department_enum, employee_pattern_file):
    app = _make_test_app(
        employee_skill, employment_type_enum, department_enum, employee_pattern_file
    )
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Enum endpoints
# ---------------------------------------------------------------------------


class TestEnumEndpoints:
    def test_list_enums(self, test_client):
        resp = test_client.get("/api/v1/enums")
        assert resp.status_code == 200
        data = resp.json()
        names = [e["name"] for e in data]
        assert "EmploymentType" in names
        assert "Department" in names

    def test_get_enum_full(self, test_client):
        resp = test_client.get("/api/v1/enums/EmploymentType")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "EmploymentType"
        assert "values" in data
        assert len(data["values"]) == 4

    def test_get_enum_not_found(self, test_client):
        resp = test_client.get("/api/v1/enums/NonExistent")
        assert resp.status_code == 404

    def test_enum_options_create_mode(self, test_client):
        resp = test_client.get("/api/v1/enums/EmploymentType/options?mode=create")
        assert resp.status_code == 200
        data = resp.json()
        values = [o["value"] for o in data["options"]]
        # INTERN is deprecated — must not appear in create mode
        assert "INTERN" not in values
        assert "FULL_TIME" in values

    def test_enum_options_edit_mode_includes_deprecated(self, test_client):
        resp = test_client.get("/api/v1/enums/EmploymentType/options?mode=edit")
        assert resp.status_code == 200
        values = [o["value"] for o in resp.json()["options"]]
        assert "INTERN" in values

    def test_enum_options_invalid_mode(self, test_client):
        resp = test_client.get("/api/v1/enums/EmploymentType/options?mode=invalid")
        assert resp.status_code == 400

    def test_enum_llm_context(self, test_client):
        resp = test_client.get("/api/v1/enums/EmploymentType/llm-context")
        assert resp.status_code == 200
        data = resp.json()
        assert "context" in data
        assert "EmploymentType" in data["context"]
        assert "DEPRECATED" in data["context"]

    def test_enum_by_group(self, test_client):
        resp = test_client.get("/api/v1/enums/by-group/hr")
        assert resp.status_code == 200
        data = resp.json()
        names = [e["name"] for e in data]
        assert "EmploymentType" in names

    def test_enum_by_group_not_found(self, test_client):
        resp = test_client.get("/api/v1/enums/by-group/nonexistent_group")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Schema endpoints
# ---------------------------------------------------------------------------


class TestSchemaEndpoints:
    def test_entity_fields(self, test_client):
        resp = test_client.get("/api/v1/schema/Employee/fields")
        assert resp.status_code == 200
        fields = resp.json()
        names = [f["name"] for f in fields]
        assert "id" in names
        assert "first_name" in names
        assert "salary" in names

    def test_entity_fields_not_found(self, test_client):
        resp = test_client.get("/api/v1/schema/NonExistent/fields")
        assert resp.status_code == 404

    def test_entity_display_config(self, test_client):
        resp = test_client.get("/api/v1/schema/Employee/display")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity"] == "Employee"
        assert "columnsVisible" in data
        assert data["pageSize"] == 25

    def test_entity_mutations_empty(self, test_client):
        # Employee has no mutations_file — should return empty list
        resp = test_client.get("/api/v1/schema/Employee/mutations")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_pk_field_marked_correctly(self, test_client):
        resp = test_client.get("/api/v1/schema/Employee/fields")
        fields = {f["name"]: f for f in resp.json()}
        assert fields["id"]["isPK"] is True
        assert fields["first_name"]["isPK"] is False

    def test_sensitive_field_flagged(self, test_client):
        resp = test_client.get("/api/v1/schema/Employee/fields")
        fields = {f["name"]: f for f in resp.json()}
        assert fields["salary"]["sensitive"] is True


# ---------------------------------------------------------------------------
# Pattern cache endpoints
# ---------------------------------------------------------------------------


class TestPatternEndpoints:
    def test_match_high_confidence_input(self, test_client):
        resp = test_client.post(
            "/api/v1/patterns/match",
            json={"input": "active employees"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is True
        assert data["pattern_id"] == "employee.active"
        assert data["confidence"] >= 0.90

    def test_match_cache_miss(self, test_client):
        resp = test_client.post(
            "/api/v1/patterns/match",
            json={"input": "zzz completely unrelated query 12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is False
        assert data["tier"] == "cache_miss"

    def test_match_with_entity_hint(self, test_client):
        resp = test_client.post(
            "/api/v1/patterns/match",
            json={"input": "contractors", "entity_hint": "Employee"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hit"] is True

    def test_match_input_too_long(self, test_client):
        resp = test_client.post(
            "/api/v1/patterns/match",
            json={"input": "x" * 501},
        )
        assert resp.status_code == 400

    def test_pattern_stats(self, test_client):
        test_client.post("/api/v1/patterns/match", json={"input": "active employees"})
        resp = test_client.get("/api/v1/patterns/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_lookups" in data
        assert "hit_rate" in data
        assert data["total_lookups"] >= 1

    def test_get_pattern_by_id(self, test_client):
        resp = test_client.get("/api/v1/patterns/employee.active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "employee.active"

    def test_get_pattern_not_found(self, test_client):
        resp = test_client.get("/api/v1/patterns/nonexistent.pattern")
        assert resp.status_code == 404

    def test_patterns_for_entity(self, test_client):
        resp = test_client.get("/api/v1/patterns/entity/Employee")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        ids = [p["id"] for p in data]
        assert "employee.active" in ids
        assert "employee.contractors" in ids

    def test_patterns_for_unknown_entity(self, test_client):
        resp = test_client.get("/api/v1/patterns/entity/NonExistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Resolve endpoint
# ---------------------------------------------------------------------------


class TestResolveEndpoint:
    def test_resolve_cache_hit(self, test_client):
        resp = test_client.post(
            "/api/v1/resolve",
            json={"input": "active employees"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "intent" in data

    def test_resolve_input_too_long(self, test_client):
        resp = test_client.post(
            "/api/v1/resolve",
            json={"input": "x" * 501},
        )
        assert resp.status_code == 400

    def test_resolve_returns_did_you_mean_for_medium_confidence(self, test_client):
        # "show me contractors please" — stopwords stripped → "contractors"
        # Should hit employee.contractors
        resp = test_client.post(
            "/api/v1/resolve",
            json={"input": "show me all contractors please"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Widgets endpoints
# ---------------------------------------------------------------------------


class TestWidgetEndpoints:
    def test_list_widgets(self, test_client):
        resp = test_client.get("/api/v1/widgets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["id"] == "active_employees"

    def test_get_widget_by_id(self, test_client):
        resp = test_client.get("/api/v1/widgets/active_employees")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity"] == "Employee"
        assert data["pattern_id"] == "employee.active"

    def test_get_widget_not_found(self, test_client):
        resp = test_client.get("/api/v1/widgets/nonexistent_widget")
        assert resp.status_code == 404

    def test_dashboard_groups_by_category(self, test_client):
        resp = test_client.get("/api/v1/widgets/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "HR" in data
        assert len(data["HR"]) >= 1

    def test_widgets_for_entity(self, test_client):
        resp = test_client.get("/api/v1/widgets/entity/Employee")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_widgets_for_unknown_entity(self, test_client):
        resp = test_client.get("/api/v1/widgets/entity/NonExistent")
        assert resp.status_code == 200
        assert resp.json() == []
