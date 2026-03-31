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
def scaffold_command(
    adapter: str,
    table: Optional[str],
    schema: str,
    output_path: Optional[str],
    output_dir: Optional[str],
    dry_run: bool,
) -> None:
    """Generate skill YAML from a live PostgreSQL table or schema."""
    from backend.skill_registry.scaffold.scaffolder import scaffold_schema, scaffold_table

    if table:
        yaml_content = asyncio.run(scaffold_table(adapter, table, schema))
        if dry_run:
            click.echo(yaml_content)
        elif output_path:
            Path(output_path).write_text(yaml_content, encoding="utf-8")
            click.echo(f"Written to {output_path}")
        else:
            click.echo(yaml_content)
    elif output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = asyncio.run(scaffold_schema(adapter, schema, out_dir if not dry_run else None))
        if dry_run:
            for tbl, content in results.items():
                click.echo(f"--- {tbl}.skill.yaml ---")
                click.echo(content)
        else:
            click.echo(f"Scaffolded {len(results)} table(s) to {output_dir}")
    else:
        click.echo("ERROR: provide --table or --output-dir", err=True)
        sys.exit(1)


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
