"""
DynamoUI backend — FastAPI application.

Startup sequence (per spec, must not be weakened):
1. Load config from environment
2. Discover all *.skill.yaml, *.enum.yaml, *.patterns.yaml, *.mutations.yaml
3. Run 4-phase validation pipeline — on any error: log full details, exit(1)
4. Build in-memory indexes
5. Load widgets.yaml — missing = warning, not fatal
6. Start FastAPI on REST_PORT
7. Emit boot metrics
"""
from __future__ import annotations

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI

from backend.skill_registry.config.settings import (
    PatternCacheSettings,
    SkillRegistrySettings,
    configure_logging,
    pg_settings,
    skill_settings,
    cache_settings,
)
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan — runs startup then shutdown."""
    await _startup(app)
    yield
    await _shutdown(app)


def create_app(
    _skill_settings: SkillRegistrySettings | None = None,
    _cache_settings: PatternCacheSettings | None = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.
    Accepts overrides for use in tests.
    """
    configure_logging(_skill_settings or skill_settings)
    app = FastAPI(
        title="DynamoUI",
        description="Adaptive Data Interface Framework",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store settings on app state for lifespan access
    app.state._skill_settings = _skill_settings or skill_settings
    app.state._cache_settings = _cache_settings or cache_settings

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    from backend.adapters.api.rest_router import router as entities_router
    from backend.pattern_cache.api.rest_router import router as patterns_router
    from backend.skill_registry.api.rest_router import router as skill_router
    from backend.skill_registry.api.widgets_router import router as widgets_router

    prefix = "/api/v1"
    app.include_router(skill_router, prefix=prefix, tags=["skill-registry"])
    app.include_router(patterns_router, prefix=prefix, tags=["pattern-cache"])
    app.include_router(entities_router, prefix=prefix, tags=["entities"])
    app.include_router(widgets_router, prefix=prefix, tags=["widgets"])


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


async def _startup(app: FastAPI) -> None:
    t0 = time.monotonic()

    s: SkillRegistrySettings = app.state._skill_settings
    cs: PatternCacheSettings = app.state._cache_settings

    log.info("dynamoui.startup_begin", skills_dir=s.skills_dir, enums_dir=s.enums_dir)

    # Step 2 — Discover YAML files
    from backend.skill_registry.loader.yaml_loader import (
        ParseError,
        discover_all,
        load_adapter_registry,
    )

    skills_dir = Path(s.skills_dir)
    enums_dir = Path(s.enums_dir)
    adapters_registry_path = Path(s.adapters_registry)

    if not adapters_registry_path.exists():
        log.error("startup.adapters_registry_missing", path=str(adapters_registry_path))
        sys.exit(1)

    try:
        adapter_reg = load_adapter_registry(adapters_registry_path)
    except ParseError as exc:
        log.error("startup.adapters_registry_parse_error", error=str(exc))
        sys.exit(1)

    discovery = discover_all(skills_dir, enums_dir)
    if discovery.errors:
        for err in discovery.errors:
            log.error("startup.parse_error", path=str(err.path), error=str(err.cause))
        log.error("startup.aborting_due_to_parse_errors", count=len(discovery.errors))
        sys.exit(1)

    # Step 3 — Run validation pipeline
    from backend.skill_registry.loader.validator import run_validation

    validation_result = run_validation(
        discovery.skills,
        discovery.enums,
        discovery.patterns,
        discovery.mutations,
        adapter_reg,
        shadow_threshold=s.fuzzy_match_shadow_threshold,
    )
    for issue in validation_result.warnings:
        log.warning("startup.validation_warning", issue=str(issue))
    if validation_result.has_errors:
        for err in validation_result.errors:
            log.error("startup.validation_error", issue=str(err))
        log.error("startup.aborting_due_to_validation_errors")
        sys.exit(1)

    # Step 4 — Build in-memory indexes
    registry = SkillRegistry(adapter_registry=adapter_reg)
    for _, skill in discovery.skills:
        registry.register_entity(skill)
    for _, enum in discovery.enums:
        registry.register_enum(enum)
    for _, pf in discovery.patterns:
        registry.register_patterns(pf)
    for _, mf in discovery.mutations:
        registry.register_mutations(mf)
    registry.build_fk_graph()

    from backend.skill_registry.registry.enum_registry import EnumRegistry
    enum_registry = EnumRegistry()
    enum_registry.register_all(list(registry.enum_by_name.values()))
    registry._enum_registry = enum_registry  # attach for router access

    app.state.skill_registry = registry

    # Step 5 — Load widgets.yaml (missing = warning, not fatal)
    _load_widgets(app)

    # Step 6 — Initialise adapters
    from backend.adapters.registry import initialise_adapters
    await initialise_adapters(str(adapters_registry_path), pg_settings)

    # Step 7 — Build pattern cache
    from backend.pattern_cache.cache.pattern_cache import PatternCache

    pattern_cache = PatternCache(
        threshold=cs.fuzzy_threshold,
        stopwords=cs.stopwords,
        enforce_skill_hash=cs.enforce_skill_hash,
        hash_length=cs.hash_length,
    )
    pattern_cache.build_from_pattern_files(
        [pf for _, pf in discovery.patterns]
    )
    app.state.pattern_cache = pattern_cache

    boot_time_ms = (time.monotonic() - t0) * 1000
    registry.boot_time_ms = boot_time_ms

    # Boot metrics
    log.info(
        "dynamoui.boot_complete",
        boot_time_ms=round(boot_time_ms, 1),
        entities_loaded=registry.entities_loaded,
        enums_loaded=registry.enums_loaded,
        patterns_loaded=registry.patterns_loaded,
    )


async def _shutdown(app: FastAPI) -> None:
    log.info("dynamoui.shutdown_begin")
    from backend.adapters.registry import _registry as adapter_registry
    for adapter in adapter_registry.values():
        if hasattr(adapter, "dispose"):
            await adapter.dispose()
    log.info("dynamoui.shutdown_complete")


def _load_widgets(app: FastAPI) -> None:
    """Load widgets.yaml. Missing file is a warning, not fatal."""
    widgets_path = Path("widgets.yaml")
    if not widgets_path.exists():
        log.warning("startup.widgets_yaml_missing", path=str(widgets_path))
        app.state.widgets = {}
        return

    import yaml
    try:
        with widgets_path.open("r", encoding="utf-8") as fh:
            app.state.widgets = yaml.safe_load(fh) or {}
        log.info("startup.widgets_loaded", count=len(app.state.widgets))
    except Exception as exc:
        log.warning("startup.widgets_load_error", error=str(exc))
        app.state.widgets = {}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=skill_settings.rest_port,
        reload=False,
    )
