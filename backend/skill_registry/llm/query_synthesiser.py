"""
QuerySynthesiser — translates natural language into a QueryPlan via LLM.
Sensitive fields are excluded from the schema context sent to the LLM.
User input is never logged raw — only its SHA-256 hash.
"""
from __future__ import annotations

import structlog

from backend.skill_registry.llm.provider import LLMProvider

log = structlog.get_logger(__name__)

SYNTHESISE_SYSTEM_PROMPT = """\
You are a query planner for a structured data system. Given a natural language
request and a schema, produce a QueryPlan as a single JSON object.

QueryPlan schema:
{
  "entity": "<primary entity, PascalCase>",
  "filters": [{"field": "<name>", "op": "<eq|ne|gt|gte|lt|lte|in|like|is_null>", "value": <any>}],
  "sort": [{"field": "<name>", "dir": "<asc|desc>"}],
  "joins": [{"source_field": "<field on primary entity>", "target_entity": "<PascalCase>",
             "target_field": "<field on target entity>", "join_type": "<inner|left>"}],
  "aggregations": [{"func": "<count|sum|avg|min|max>", "field": "<name or *>", "alias": "<name>"}],
  "group_by": ["<field>"],
  "result_limit": <integer or null>,
  "page": 1,
  "page_size": 25,
  "select_fields": []
}

Rules (must follow exactly):
1. Use only entities and fields present in the schema below.
2. For TOP N queries: set result_limit=N, add an aggregation, sort by the
   aggregated alias descending.
3. Multi-hop joins: declare every intermediate join in order
   (e.g. InvoiceLine→Track→Album requires two join entries).
4. Sensitive fields are already excluded from the schema — do not reference them.
5. Respond with ONLY the JSON object. No markdown, no explanation.
"""


class QuerySynthesiser:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def _build_schema_context(self, registry: "SkillRegistry") -> str:
        """
        Compact plain-text schema for the LLM prompt. One block per entity.
        Sensitive fields are excluded.
        """
        lines = []
        for entity_name, skill in sorted(registry.entity_by_name.items()):
            field_parts = []
            for f in skill.fields:
                if f.sensitive:
                    continue
                suffix = " PK" if f.isPK else ""
                field_parts.append(f"{f.name} ({f.type}{suffix})")
            lines.append(f"Entity: {entity_name} (table: {skill.table})")
            lines.append(f"Fields: {', '.join(field_parts)}")
            fk_edges = registry.fk_graph.get(entity_name, [])
            if fk_edges:
                fk_parts = [f"{src} -> {tgt}.{tgt_field}"
                            for src, tgt, tgt_field in fk_edges]
                lines.append(f"FK: {', '.join(fk_parts)}")
            lines.append("")
        return "\n".join(lines)

    async def synthesise(
        self,
        user_input: str,
        registry: "SkillRegistry",
    ) -> "tuple[QueryPlan, float] | None":
        import hashlib
        import json
        from backend.adapters.base import (
            QueryPlan, FilterClause, SortClause, JoinClause, AggregationClause
        )

        input_hash = hashlib.sha256(user_input.encode()).hexdigest()[:16]
        schema_context = self._build_schema_context(registry)
        user_prompt = f"Schema:\n{schema_context}\nRequest: {user_input}"

        try:
            from backend.skill_registry.llm.provider import strip_markdown_json
            raw = await self._provider.complete(SYNTHESISE_SYSTEM_PROMPT, user_prompt)
            if not raw:
                return None
            data = json.loads(strip_markdown_json(raw))
            plan = _parse_query_plan(data)
        except Exception as exc:
            log.warning("query_synthesiser.failed",
                        input_hash=input_hash, error=str(exc))
            return None

        # Deterministic confidence scoring
        confidence = 0.85  # baseline: valid JSON response

        if plan.entity in registry.entity_by_name:
            confidence += 0.05
        else:
            confidence -= 0.10

        # Check join entities
        all_join_entities_known = all(
            j.target_entity in registry.entity_by_name for j in plan.joins
        )
        unknown_join_entities = [
            j.target_entity for j in plan.joins
            if j.target_entity not in registry.entity_by_name
        ]
        if plan.joins and all_join_entities_known:
            confidence += 0.03
        for _ in unknown_join_entities:
            confidence -= 0.10

        # Check filter fields on primary entity
        primary_skill = registry.entity_by_name.get(plan.entity)
        if primary_skill and plan.filters:
            primary_field_names = {f.name for f in primary_skill.fields}
            if all(f.field in primary_field_names for f in plan.filters):
                confidence += 0.03

        confidence = max(0.0, min(1.0, confidence))
        return plan, confidence


def _parse_query_plan(data: dict) -> "QueryPlan":
    """
    Convert raw LLM JSON dict to a QueryPlan dataclass.
    Raises ValueError on structural problems so the caller can catch and return None.
    """
    from backend.adapters.base import (
        QueryPlan, FilterClause, SortClause, JoinClause, AggregationClause
    )
    return QueryPlan(
        entity=data["entity"],
        filters=[FilterClause(**f) for f in data.get("filters", [])],
        sort=[SortClause(**s) for s in data.get("sort", [])],
        joins=[JoinClause(**j) for j in data.get("joins", [])],
        aggregations=[AggregationClause(**a) for a in data.get("aggregations", [])],
        group_by=data.get("group_by", []),
        result_limit=data.get("result_limit"),
        page=data.get("page", 1),
        page_size=data.get("page_size", 25),
        select_fields=data.get("select_fields", []),
    )
