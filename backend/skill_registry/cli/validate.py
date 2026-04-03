"""
Click CLI entry point — dynamoui validate / scaffold / compile-patterns
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Optional

import click
import structlog

from backend.skill_registry.config.settings import (
    SkillRegistrySettings,
    configure_logging,
    skill_settings,
)
from backend.skill_registry.loader.yaml_loader import (
    DiscoveryResult,
    ParseError,
    discover_all,
    load_adapter_registry,
    load_patterns,
    load_skill,
)
from backend.skill_registry.loader.validator import ValidationResult, run_validation
from backend.skill_registry.models.registry import SkillRegistry

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
def cli() -> None:
    """DynamoUI management CLI."""
    configure_logging(skill_settings)


# ---------------------------------------------------------------------------
# dynamoui validate
# ---------------------------------------------------------------------------


@cli.command("validate")
@click.option("--skills-dir", default=None, help="Directory containing *.skill.yaml files")
@click.option("--enums-dir", default=None, help="Directory containing *.enum.yaml files")
@click.option("--adapters-registry", default=None, help="Path to adapters.registry.yaml")
@click.option("--file", "single_file", default=None, help="Validate a single skill file")
@click.option(
    "--check-connectivity",
    is_flag=True,
    default=False,
    help="Run Phase 4: live DB connectivity check",
)
@click.option(
    "--output",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
def validate_command(
    skills_dir: Optional[str],
    enums_dir: Optional[str],
    adapters_registry: Optional[str],
    single_file: Optional[str],
    check_connectivity: bool,
    output: str,
) -> None:
    """Run the 4-phase validation pipeline against skill files."""
    settings = SkillRegistrySettings()
    _skills_dir = Path(skills_dir or settings.skills_dir)
    _enums_dir = Path(enums_dir or settings.enums_dir)
    _adapters_registry = Path(adapters_registry or settings.adapters_registry)

    parse_errors: list[ParseError] = []

    # Load adapter registry
    if not _adapters_registry.exists():
        click.echo(f"ERROR: adapters registry not found: {_adapters_registry}", err=True)
        sys.exit(1)

    try:
        adapter_reg = load_adapter_registry(_adapters_registry)
    except ParseError as exc:
        click.echo(f"ERROR parsing adapters registry: {exc}", err=True)
        sys.exit(1)

    if single_file:
        # Single-file mode
        path = Path(single_file)
        if not path.exists():
            click.echo(f"ERROR: file not found: {path}", err=True)
            sys.exit(1)
        try:
            skill = load_skill(path)
            skills = [(path, skill)]
        except ParseError as exc:
            click.echo(f"ERROR parsing {path}: {exc}", err=True)
            sys.exit(1)
        enums_result: list = []
        patterns_result: list = []
        mutations_result: list = []
    else:
        discovery = discover_all(_skills_dir, _enums_dir)
        skills = discovery.skills
        enums_result = discovery.enums
        patterns_result = discovery.patterns
        mutations_result = discovery.mutations
        parse_errors = discovery.errors

    validation_result = run_validation(
        skills,
        enums_result,
        patterns_result,
        mutations_result,
        adapter_reg,
        shadow_threshold=settings.fuzzy_match_shadow_threshold,
        check_connectivity=check_connectivity,
    )

    # Add parse errors as phase 1 errors
    all_issues = [
        {"phase": 1, "severity": "error", "path": str(e.path), "message": str(e.cause)}
        for e in parse_errors
    ] + [
        {"phase": i.phase, "severity": i.severity, "path": i.path, "message": i.message}
        for i in validation_result.issues
    ]

    has_errors = parse_errors or validation_result.has_errors

    if output == "json":
        click.echo(json.dumps({"issues": all_issues, "success": not has_errors}, indent=2))
    else:
        for issue in all_issues:
            severity = issue["severity"].upper()
            click.echo(
                f"[Phase {issue['phase']}] {severity} {issue['path']}: {issue['message']}"
            )
        click.echo(
            f"\n{'PASSED' if not has_errors else 'FAILED'} — "
            f"{len([i for i in all_issues if i['severity']=='error'])} error(s), "
            f"{len([i for i in all_issues if i['severity']=='warning'])} warning(s)"
        )

    sys.exit(1 if has_errors else 0)


# ---------------------------------------------------------------------------
# dynamoui scaffold
# ---------------------------------------------------------------------------


@cli.command("scaffold")
@click.option(
    "--adapter",
    required=True,
    help="Adapter key from adapters.registry.yaml (e.g. 'postgresql')",
)
@click.option("--table", default=None, help="Table name to scaffold")
@click.option("--schema", default="public", help="PostgreSQL schema name")
@click.option("--output", "output_path", default=None, help="Output file path")
@click.option("--output-dir", default=None, help="Output directory for multi-table scaffold")
@click.option("--dry-run", is_flag=True, default=False, help="Print YAML without writing to disk")
@click.option(
    "--seed-patterns",
    is_flag=True,
    default=False,
    help="Use LLM to seed cross-entity patterns (requires DYNAMO_LLM_* env vars)",
)
@click.option(
    "--llm-batch-size",
    default=5,
    show_default=True,
    type=int,
    help="Number of entities per LLM call when --seed-patterns is active",
)
def scaffold_command(
    adapter: str,
    table: Optional[str],
    schema: str,
    output_path: Optional[str],
    output_dir: Optional[str],
    dry_run: bool,
    seed_patterns: bool,
    llm_batch_size: int,
) -> None:
    """Generate skill YAML from a live PostgreSQL table or schema."""
    from backend.adapters.registry import initialise_adapters
    from backend.skill_registry.config.settings import pg_settings, skill_settings
    from backend.skill_registry.scaffold.scaffolder import scaffold_schema, scaffold_table

    async def _run() -> None:
        await initialise_adapters(skill_settings.adapters_registry, pg_settings)

        seeder = None
        if seed_patterns:
            from backend.skill_registry.config.settings import llm_settings
            from backend.skill_registry.llm.provider import create_provider
            from backend.skill_registry.llm.pattern_seeder import PatternSeeder
            provider = create_provider(llm_settings)
            seeder = PatternSeeder(provider)
            click.echo("LLM pattern seeding enabled.")

        if table:
            _single_out_dir = Path(output_path).parent if output_path else None
            output = await scaffold_table(adapter, table, schema, _single_out_dir, llm_seeder=seeder)
            if dry_run or not output_path:
                click.echo(output.skill_yaml)
                click.echo(f"--- {table}.patterns.yaml ---")
                click.echo(output.patterns_yaml)
            else:
                skill_p = Path(output_path)
                skill_p.write_text(output.skill_yaml, encoding="utf-8")
                patterns_p = skill_p.parent / f"{table}.patterns.yaml"
                patterns_p.write_text(output.patterns_yaml, encoding="utf-8")
                widgets_p = skill_p.parent / "widgets.yaml"
                import yaml as _yaml
                existing: list = []
                if widgets_p.exists():
                    existing = (_yaml.safe_load(widgets_p.read_text(encoding="utf-8")) or {}).get("widgets", [])
                existing.extend(output.widgets)
                widgets_p.write_text(
                    _yaml.dump({"widgets": existing}, default_flow_style=False, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
                click.echo(f"Written: {skill_p}, {patterns_p}, {widgets_p}")
        elif output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            results = await scaffold_schema(adapter, schema, out_dir if not dry_run else None, llm_seeder=seeder, llm_batch_size=llm_batch_size)
            if dry_run:
                for tbl, output in results.items():
                    click.echo(f"--- {tbl}.skill.yaml ---")
                    click.echo(output.skill_yaml)
                    click.echo(f"--- {tbl}.patterns.yaml ---")
                    click.echo(output.patterns_yaml)
            else:
                click.echo(f"Scaffolded {len(results)} table(s) to {output_dir} (skill, patterns, widgets)")
        else:
            click.echo("ERROR: provide --table or --output-dir", err=True)
            sys.exit(1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# dynamoui setup
# ---------------------------------------------------------------------------


@cli.command("setup")
@click.option("--env-file", "env_file", default=".env", show_default=True, help="Path to write the .env file")
@click.option("--no-test", is_flag=True, default=False, help="Skip connection test")
def setup_command(env_file: str, no_test: bool) -> None:
    """Interactive onboarding: configure PostgreSQL connection and write a .env file."""
    env_path = Path(env_file)

    click.echo("DynamoUI setup — configure your PostgreSQL connection.")
    click.echo("Press Enter to accept the default shown in [brackets].\n")

    if env_path.exists():
        overwrite = click.confirm(f"{env_path} already exists. Overwrite?", default=False)
        if not overwrite:
            click.echo("Aborted.")
            sys.exit(0)

    host = click.prompt("DB host", default="localhost")
    port = click.prompt("DB port", default=5432, type=int)
    database = click.prompt("DB name", default="dynamoui")

    click.echo("\n-- Read-only user (used for all SELECT queries) --")
    read_user = click.prompt("Read user", default="dynamoui_reader")
    read_password = click.prompt("Read password", hide_input=True, confirmation_prompt=True)

    click.echo("\n-- Write user (used for mutation operations only) --")
    write_user = click.prompt("Write user", default="dynamoui_writer")
    write_password = click.prompt("Write password", hide_input=True, confirmation_prompt=True)

    ssl_mode = click.prompt(
        "\nSSL mode",
        default="prefer",
        type=click.Choice(["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]),
    )

    if not no_test:
        click.echo("\nTesting connection...")
        error = asyncio.run(_test_pg_connection(host, port, database, read_user, read_password))
        if error:
            click.echo(f"  Connection failed: {error}", err=True)
            if not click.confirm("Write .env anyway?", default=False):
                click.echo("Aborted.")
                sys.exit(1)
        else:
            click.echo("  Connection successful.")

    lines = [
        "# DynamoUI PostgreSQL configuration — generated by `dynamoui setup`",
        f"DYNAMO_PG_HOST={host}",
        f"DYNAMO_PG_PORT={port}",
        f"DYNAMO_PG_DATABASE={database}",
        f"DYNAMO_PG_USER={read_user}",
        f"DYNAMO_PG_PASSWORD={read_password}",
        f"DYNAMO_PG_WRITE_USER={write_user}",
        f"DYNAMO_PG_WRITE_PASSWORD={write_password}",
        f"DYNAMO_PG_SSL_MODE={ssl_mode}",
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    click.echo(f"\nWritten to {env_path}")
    click.echo("Run `dynamoui validate --check-connectivity` to verify your skill files against the live DB.")


async def _test_pg_connection(
    host: str, port: int, database: str, user: str, password: str
) -> str | None:
    """Return None on success, or an error message string on failure."""
    try:
        import asyncpg
        conn = await asyncpg.connect(
            host=host, port=port, database=database, user=user, password=password,
            timeout=10,
        )
        await conn.close()
        return None
    except Exception as exc:
        return str(exc)


# ---------------------------------------------------------------------------
# dynamoui compile-patterns
# ---------------------------------------------------------------------------


@cli.command("compile-patterns")
@click.option("--skills-dir", default=None, help="Directory containing *.skill.yaml files")
def compile_patterns_command(skills_dir: Optional[str]) -> None:
    """Recompute skill_hash headers in all *.patterns.yaml files."""
    from backend.pattern_cache.versioning.hasher import PatternHasher

    settings = SkillRegistrySettings()
    _skills_dir = Path(skills_dir or settings.skills_dir)

    updated = 0
    skipped = 0

    for skill_path in sorted(_skills_dir.glob("*.skill.yaml")):
        # Derive patterns file path from skill file name
        entity_stem = skill_path.stem.replace(".skill", "")
        patterns_path = _skills_dir / f"{entity_stem}.patterns.yaml"

        if not patterns_path.exists():
            log.debug("compile_patterns.no_patterns_file", skill=str(skill_path))
            continue

        current_hash = PatternHasher.compute_skill_hash(skill_path)
        text = patterns_path.read_text(encoding="utf-8")
        lines = text.split("\n")

        if lines and lines[0].startswith("# skill_hash:"):
            stored_hash = lines[0].split("skill_hash:")[1].strip()
            if stored_hash == current_hash:
                skipped += 1
                continue
            # Update existing header
            lines[0] = f"# skill_hash: {current_hash}"
        else:
            # Prepend header
            lines = [f"# skill_hash: {current_hash}"] + lines

        patterns_path.write_text("\n".join(lines), encoding="utf-8")
        click.echo(f"Updated: {patterns_path.name} (hash: {current_hash})")
        updated += 1

    click.echo(f"\nDone: {updated} updated, {skipped} already current.")
