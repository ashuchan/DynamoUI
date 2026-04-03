"""
PatternSeeder — LLM-assisted scaffold pattern generation.
Invoked by `dynamoui scaffold --seed-patterns`. Output is written to the patterns
YAML file alongside the skill file. Operator reviews before committing.
Sensitive fields are excluded from the schema context sent to the LLM.
"""
from __future__ import annotations

import structlog

from backend.skill_registry.llm.provider import LLMProvider

log = structlog.get_logger(__name__)

SEED_SYSTEM_PROMPT = """\
You are generating query patterns for a data management system.
Given the complete schema of a database, suggest meaningful read query patterns
for the specified entity. Include cross-entity patterns that traverse FK relationships.

For each pattern provide:
- id: dot.separated.snake_case globally unique identifier (prefix: <entity_lower>.)
- description: one sentence
- triggers: 3-5 natural language phrases a user might type (varied phrasing)
- query_template: JSON object with keys: filters, sort, joins, aggregations, group_by, result_limit
- params: list of {name, type, required, default} if the template has placeholders

Focus on:
1. Simple list/filter patterns for the entity itself
2. Join patterns using direct FK relationships (parent-child)
3. Aggregation patterns using multi-hop FK chains (e.g. count purchases grouped by product)
4. Ranking patterns (top N by some measure)

Respond with a JSON array of pattern objects. No markdown. No explanation.
"""

BATCH_SEED_SYSTEM_PROMPT = """\
You are generating query patterns for a data management system.
Given the complete database schema, generate meaningful read query patterns for
MULTIPLE entities listed below. Include cross-entity patterns using FK relationships.

For each pattern provide:
- id: dot.separated.snake_case globally unique identifier (prefix: <entity_lower>.)
- description: one sentence
- triggers: 3-5 natural language phrases a user might type (varied phrasing)
- query_template: JSON object with keys: filters, sort, joins, aggregations, group_by, result_limit
- params: list of {name, type, required, default} if the template has placeholders

Focus on (in order of priority):
1. One join pattern using a direct FK relationship (cross-entity)
2. One aggregation or ranking pattern (top N, count by group)
3. One simple filter pattern

Generate at most 3 patterns per entity. Omit any entity you have nothing meaningful to add beyond list_all.

Respond with a single JSON object where each key is an entity name (PascalCase) and
the value is an array of pattern objects for that entity.
No markdown. No explanation. Example shape:
{"Album": [...patterns], "Artist": [...patterns]}
"""


class PatternSeeder:
    """
    Invoked by `dynamoui scaffold` to seed cross-entity patterns using the LLM.
    The output is written to the patterns YAML file alongside the skill file.
    Operator reviews the file before committing — this is not auto-executed at runtime.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def seed_patterns(
        self,
        entity: str,
        skill_yaml: str,
        full_schema_context: str,
    ) -> list[dict]:
        """
        Call the LLM once with the full schema and return a list of pattern dicts.
        Returns [] on any failure so the scaffolder can fall back to basic patterns.
        """
        import json
        from backend.skill_registry.llm.provider import strip_markdown_json
        user_prompt = (
            f"Full schema:\n{full_schema_context}\n\n"
            f"Generate patterns for entity: {entity}\n"
            f"Skill YAML:\n{skill_yaml}"
        )
        try:
            raw = await self._provider.complete(SEED_SYSTEM_PROMPT, user_prompt)
            if not raw:
                return []
            data = json.loads(strip_markdown_json(raw))
            if not isinstance(data, list):
                return []
            return data
        except Exception as exc:
            log.warning("pattern_seeder.failed", entity=entity, error=str(exc))
            return []

    async def seed_patterns_batch(
        self,
        batch_skill_yamls: dict[str, str],
        full_schema_context: str,
    ) -> dict[str, list[dict]]:
        """
        Call the LLM once for a batch of entities and return {entity: [patterns]}.
        Returns {} on any failure so the caller can skip the batch gracefully.
        batch_skill_yamls: {entity_name: skill_yaml_string} for entities in this batch.
        """
        import json
        from backend.skill_registry.llm.provider import strip_markdown_json

        entity_list = ", ".join(sorted(batch_skill_yamls.keys()))
        skill_block = "\n\n".join(
            f"Entity: {entity}\n{yaml_str}"
            for entity, yaml_str in sorted(batch_skill_yamls.items())
        )
        user_prompt = (
            f"Full schema:\n{full_schema_context}\n\n"
            f"Generate patterns for these entities: {entity_list}\n\n"
            f"{skill_block}"
        )
        try:
            raw = await self._provider.complete(BATCH_SEED_SYSTEM_PROMPT, user_prompt)
            if not raw:
                return {}
            data = json.loads(strip_markdown_json(raw))
            if not isinstance(data, dict):
                log.warning("pattern_seeder.batch_unexpected_shape",
                            entities=entity_list, type=type(data).__name__)
                return {}
            # Validate each value is a list
            return {k: v for k, v in data.items() if isinstance(v, list)}
        except Exception as exc:
            log.warning("pattern_seeder.batch_failed",
                        entities=entity_list, error=str(exc))
            return {}

    def build_full_schema_context(
        self, all_skill_yamls: dict[str, str], fk_edges: dict[str, list]
    ) -> str:
        """
        Build the compact schema string used by QuerySynthesiser, but from raw YAML
        strings rather than a live SkillRegistry (registry doesn't exist during scaffold).
        Sensitive fields are excluded.

        all_skill_yamls: {entity_name: skill_yaml_string}
        fk_edges: {entity_name: [(source_field, target_entity, target_field)]}
        """
        import yaml
        lines = []
        for entity_name, skill_yaml_str in sorted(all_skill_yamls.items()):
            raw = yaml.safe_load(skill_yaml_str) or {}
            fields = raw.get("fields", [])
            non_sensitive = [f for f in fields if not f.get("sensitive", False)]
            field_parts = [
                f"{f['name']} ({f['type']}{' PK' if f.get('isPK') else ''})"
                for f in non_sensitive
            ]
            lines.append(f"Entity: {entity_name} (table: {raw.get('table', '')})")
            lines.append(f"Fields: {', '.join(field_parts)}")
            edges = fk_edges.get(entity_name, [])
            if edges:
                lines.append("FK: " + ", ".join(
                    f"{src} -> {tgt}.{tgt_f}" for src, tgt, tgt_f in edges
                ))
            lines.append("")
        return "\n".join(lines)
