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
    internal_settings,
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
    from backend.auth.api.routes import router as auth_router
    from backend.metering.api.routes import router as metering_router
    from backend.pattern_cache.api.rest_router import router as patterns_router
    from backend.skill_registry.api.rest_router import router as skill_router
    from backend.skill_registry.api.widgets_router import router as widgets_router
    from backend.tenants.connections.routes import router as connections_router
    from backend.tenants.scaffold.routes import router as scaffold_router

    prefix = "/api/v1"
    app.include_router(auth_router, prefix=prefix, tags=["auth"])
    app.include_router(connections_router, prefix=prefix, tags=["admin-connections"])
    app.include_router(scaffold_router, prefix=prefix, tags=["admin-scaffold"])
    app.include_router(skill_router, prefix=prefix, tags=["skill-registry"])
    app.include_router(patterns_router, prefix=prefix, tags=["pattern-cache"])
    app.include_router(entities_router, prefix=prefix, tags=["entities"])
    app.include_router(widgets_router, prefix=prefix, tags=["widgets"])
    app.include_router(metering_router, prefix=f"{prefix}/metering", tags=["metering"])


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
    await initialise_adapters(str(adapters_registry_path), pg_settings, skill_registry=registry)

    # Step 7 — Initialise metering + auth + connections subsystems
    from backend.auth.models.tables import configure_schema as configure_auth_schema
    from backend.metering.models.tables import configure_schema
    from backend.metering.service import create_metering_service
    from backend.adapters.postgresql.engine import PostgreSQLEngine as _PGEngine
    from backend.tenants.connections.tables import (
        configure_schema as configure_connections_schema,
    )
    from backend.tenants.scaffold.tables import (
        configure_schema as configure_scaffold_schema,
    )

    configure_schema(internal_settings.db_schema)
    configure_auth_schema(internal_settings.db_schema)
    configure_connections_schema(internal_settings.db_schema)
    configure_scaffold_schema(internal_settings.db_schema)

    metering_service = None
    try:
        metering_db_url = internal_settings.resolved_db_url(pg_settings)
        from sqlalchemy.ext.asyncio import create_async_engine as _create_engine
        metering_engine = _create_engine(
            metering_db_url,
            pool_size=2,
            max_overflow=3,
            pool_recycle=3600,
        )
        metering_service = create_metering_service(metering_engine)
        app.state.metering_service = metering_service
        log.info("metering.initialised", schema=internal_settings.db_schema)
    except Exception as exc:
        log.warning("metering.init_failed", error=str(exc))
        app.state.metering_service = None

    # Step 8 — Initialise auth subsystem (shares the internal pool when possible)
    from backend.auth.config import auth_settings
    from backend.auth.dao import AuthDAO
    from backend.auth.service import AuthService

    try:
        auth_db_url = internal_settings.resolved_db_url(pg_settings)
        from sqlalchemy.ext.asyncio import create_async_engine as _create_auth_engine

        auth_engine = _create_auth_engine(
            auth_db_url, pool_size=2, max_overflow=3, pool_recycle=3600
        )
        auth_dao = AuthDAO(auth_engine)
        app.state.auth_dao = auth_dao
        app.state.auth_engine = auth_engine
        app.state.auth_service = AuthService(auth_dao, settings=auth_settings)
        log.info("auth.initialised", schema=internal_settings.db_schema)

        # Connections share the same engine — they live in the same schema.
        from backend.tenants.connections.dao import ConnectionDAO
        from backend.tenants.connections.service import ConnectionService
        from backend.tenants.scaffold.dao import ScaffoldJobDAO
        from backend.tenants.scaffold.service import ScaffoldService

        connection_dao = ConnectionDAO(auth_engine)
        connection_service = ConnectionService(connection_dao)
        app.state.connection_dao = connection_dao
        app.state.connection_service = connection_service

        scaffold_dao = ScaffoldJobDAO(auth_engine)
        app.state.scaffold_dao = scaffold_dao
        app.state.scaffold_service = ScaffoldService(
            dao=scaffold_dao, connection_service=connection_service
        )
        log.info("connections.initialised")
    except Exception as exc:  # noqa: BLE001
        log.warning("auth.init_failed", error=str(exc))
        app.state.auth_dao = None
        app.state.auth_service = None
        app.state.auth_engine = None
        app.state.connection_service = None
        app.state.scaffold_service = None

    # Step 9 — Build pattern cache
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

    # Step 10 — Wire LLM provider, QuerySynthesiser, PatternPromoter
    from backend.skill_registry.config.settings import llm_settings
    from backend.skill_registry.llm.provider import create_provider
    from backend.skill_registry.llm.query_synthesiser import QuerySynthesiser
    from backend.pattern_cache.promotion.promoter import PatternPromoter

    llm_provider = create_provider(llm_settings)

    # Wrap with metering decorator when metering is available
    if metering_service is not None:
        from backend.metering.provider_decorator import MeteringLLMProvider
        provider_name = llm_settings.provider
        model = (
            llm_settings.anthropic_model
            if provider_name == "anthropic"
            else llm_settings.google_model
        )
        llm_provider = MeteringLLMProvider(
            inner=llm_provider,
            metering_service=metering_service,
            provider_name=provider_name,
            model=model,
        )
        log.info("metering.provider_wrapped", provider=provider_name, model=model)

    app.state.query_synthesiser = QuerySynthesiser(llm_provider)

    def _on_pattern_promoted(entity: str, path: Path) -> None:
        from backend.skill_registry.loader.yaml_loader import load_patterns
        from backend.pattern_cache.loader.pattern_loader import PatternLoader
        try:
            pf = load_patterns(path)
            loader = PatternLoader(enforce_skill_hash=False)
            entries = loader.build_trigger_entries(
                [pf], app.state.pattern_cache._stopwords
            )
            existing = app.state.pattern_cache._index.all_entries()
            merged = [e for e in existing if e.entity != entity] + entries
            app.state.pattern_cache._index.build(merged)
            app.state.pattern_cache._pattern_by_id.update(
                {p.id: pf for p in pf.patterns}
            )
            log.info("pattern_cache.hot_reloaded", entity=entity, new_entries=len(entries))
        except Exception as exc:
            log.warning("pattern_cache.hot_reload_failed", entity=entity, error=str(exc))

    app.state.pattern_promoter = PatternPromoter(
        skills_dir=skills_dir,
        auto_promote_enabled=llm_settings.auto_promote_enabled,
        auto_promote_threshold=llm_settings.auto_promote_threshold,
        review_queue_threshold=llm_settings.review_queue_threshold,
        review_queue_path=Path(llm_settings.review_queue_path),
        on_promote_callback=_on_pattern_promoted,
    )

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
    # Dispose metering engine if it was created separately
    metering_svc = getattr(app.state, "metering_service", None)
    if metering_svc is not None:
        try:
            engine = metering_svc._ops._engine
            await engine.dispose()
        except Exception:
            pass
    # Dispose auth engine
    auth_engine = getattr(app.state, "auth_engine", None)
    if auth_engine is not None:
        try:
            await auth_engine.dispose()
        except Exception:
            pass
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
