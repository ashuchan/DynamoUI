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
request and a database schema, produce a QueryPlan as a single JSON object.

QueryPlan schema:
{
  "entity": "<primary entity name, PascalCase, e.g. Album>",
  "filters": [
    {"field": "<snake_case field name>", "op": "<eq|ne|gt|gte|lt|lte|in|like|is_null>", "value": <any>}
  ],
  "sort": [{"field": "<snake_case field name OR aggregation alias>", "dir": "<asc|desc>"}],
  "joins": [
    {
      "source_field": "<snake_case field on the primary entity that links to target>",
      "target_entity": "<PascalCase entity name>",
      "target_field": "<snake_case field on the target entity being joined to>",
      "join_type": "<inner|left>"
    }
  ],
  "aggregations": [
    {"func": "<count|sum|avg|min|max>", "field": "<snake_case field name or *>", "alias": "<result column name>"}
  ],
  "group_by": ["<snake_case field name>"],
  "result_limit": <integer or null>,
  "page": 1,
  "page_size": 25,
  "select_fields": []
}

Rules (follow exactly):
1. All field names are snake_case exactly as listed in the schema. Never use PascalCase column names.
2. Never qualify field names with table prefixes (write "album_id" not "album.album_id").
3. For TOP N queries: set result_limit=N, declare the aggregation, sort descending by the aggregation alias.
4. Multi-hop joins: list every intermediate join in traversal order.
   Example — to reach Album from InvoiceLine: InvoiceLine→Track (track_id→track_id), then Track→Album (album_id→album_id).
5. group_by must list the non-aggregated fields in select (all fields that appear in select_fields or are needed in output).
6. Sensitive fields are already excluded from the schema — do not reference them.
7. Respond with ONLY the JSON object. No markdown fences, no explanation.

Example — "top 5 albums most purchased":
{
  "entity": "Album",
  "filters": [],
  "sort": [{"field": "purchase_count", "dir": "desc"}],
  "joins": [
    {"source_field": "album_id", "target_entity": "Track", "target_field": "album_id", "join_type": "inner"},
    {"source_field": "track_id", "target_entity": "InvoiceLine", "target_field": "track_id", "join_type": "inner"}
  ],
  "aggregations": [{"func": "sum", "field": "quantity", "alias": "purchase_count"}],
  "group_by": ["album_id", "title"],
  "result_limit": 5,
  "page": 1,
  "page_size": 25,
  "select_fields": []
}
"""


class QuerySynthesiser:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def _build_schema_context(self, registry: "SkillRegistry") -> str:
        """
        Enriched plain-text schema for the LLM prompt. One block per entity.
        Includes business_description, field descriptions, and semantic tags when present.
        Sensitive fields are always excluded.
        """
        lines = []
        for entity_name, skill in sorted(registry.entity_by_name.items()):
            non_sensitive = [f for f in skill.fields if not f.sensitive]
            lines.append(f"Entity: {entity_name} (table: {skill.table})")

            if skill.business_description:
                lines.append(f"Business context: {skill.business_description}")

            has_enrichment = any(f.description or f.semantic for f in non_sensitive)
            if has_enrichment:
                lines.append("Fields:")
                for f in non_sensitive:
                    type_parts = [f.type]
                    if f.isPK:
                        type_parts.append("PK")
                    if f.semantic:
                        type_parts.append(f.semantic)
                    type_str = ", ".join(type_parts)
                    field_line = f"  {f.name} ({type_str})"
                    if f.description:
                        field_line += f" — {f.description}"
                    lines.append(field_line)
            else:
                field_parts = []
                for f in non_sensitive:
                    suffix = " PK" if f.isPK else ""
                    field_parts.append(f"{f.name} ({f.type}{suffix})")
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

        input_hash = hashlib.sha256(user_input.encode()).hexdigest()[:16]
        schema_context = self._build_schema_context(registry)
        user_prompt = f"Schema:\n{schema_context}\nRequest: {user_input}"

        log.debug(
            "query_synthesiser.prompt",
            input_hash=input_hash,
            system_prompt_chars=len(SYNTHESISE_SYSTEM_PROMPT),
            user_prompt_chars=len(user_prompt),
            user_prompt=user_prompt,
        )

        try:
            from backend.skill_registry.llm.provider import strip_markdown_json
            llm_response = await self._provider.complete(SYNTHESISE_SYSTEM_PROMPT, user_prompt)
            raw = llm_response.text

            log.debug(
                "query_synthesiser.raw_response",
                input_hash=input_hash,
                response=raw,
            )

            if not raw:
                log.warning("query_synthesiser.empty_response", input_hash=input_hash)
                return None

            data = json.loads(strip_markdown_json(raw))
            plan = _parse_query_plan(data)

        except Exception as exc:
            log.warning(
                "query_synthesiser.failed",
                input_hash=input_hash,
                error=str(exc),
            )
            return None

        # Confidence scoring
        confidence = 0.85

        if plan.entity in registry.entity_by_name:
            confidence += 0.05
        else:
            log.warning(
                "query_synthesiser.unknown_entity",
                input_hash=input_hash,
                entity=plan.entity,
            )
            confidence -= 0.10

        unknown_join_entities = [
            j.target_entity for j in plan.joins
            if j.target_entity not in registry.entity_by_name
        ]
        if plan.joins and not unknown_join_entities:
            confidence += 0.03
        for _ in unknown_join_entities:
            log.warning(
                "query_synthesiser.unknown_join_entity",
                input_hash=input_hash,
                entity=plan.entity,
            )
            confidence -= 0.10

        primary_skill = registry.entity_by_name.get(plan.entity)
        if primary_skill and plan.filters:
            primary_field_names = {f.name for f in primary_skill.fields}
            if all(f.field in primary_field_names for f in plan.filters):
                confidence += 0.03

        confidence = max(0.0, min(1.0, confidence))

        log.info(
            "query_synthesiser.plan",
            input_hash=input_hash,
            entity=plan.entity,
            filters=len(plan.filters),
            joins=len(plan.joins),
            aggregations=len(plan.aggregations),
            group_by=plan.group_by,
            result_limit=plan.result_limit,
            confidence=round(confidence, 3),
        )

        return plan, confidence


def _parse_query_plan(data: dict) -> "QueryPlan":
    """
    Convert raw LLM JSON dict to a QueryPlan dataclass.
    Raises on structural problems so the caller can catch and return None.
    """
    from backend.adapters.base import (
        QueryPlan, FilterClause, SortClause, JoinClause, AggregationClause
    )

    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    entity = data.get("entity")
    if not entity or not isinstance(entity, str):
        raise ValueError("QueryPlan missing 'entity' string field")

    filters = []
    for f in data.get("filters", []) or []:
        if not isinstance(f, dict):
            continue
        if "field" not in f or "op" not in f:
            continue
        filters.append(FilterClause(
            field=str(f["field"]),
            op=str(f["op"]),
            value=f.get("value"),
        ))

    sort = []
    for s in data.get("sort", []) or []:
        if not isinstance(s, dict) or "field" not in s:
            continue
        sort.append(SortClause(
            field=str(s["field"]),
            dir=str(s.get("dir", "asc")).lower(),
        ))

    joins = []
    for j in data.get("joins", []) or []:
        if not isinstance(j, dict):
            continue
        if not all(k in j for k in ("source_field", "target_entity", "target_field")):
            continue
        jt = str(j.get("join_type", "inner")).lower()
        if jt not in ("inner", "left"):
            jt = "inner"
        joins.append(JoinClause(
            source_field=str(j["source_field"]),
            target_entity=str(j["target_entity"]),
            target_field=str(j["target_field"]),
            join_type=jt,
        ))

    aggregations = []
    for a in data.get("aggregations", []) or []:
        if not isinstance(a, dict):
            continue
        if not all(k in a for k in ("func", "field", "alias")):
            continue
        func = str(a["func"]).lower()
        if func not in ("count", "sum", "avg", "min", "max"):
            continue
        aggregations.append(AggregationClause(
            func=func,
            field=str(a["field"]),
            alias=str(a["alias"]),
        ))

    result_limit = data.get("result_limit")
    if result_limit is not None:
        try:
            result_limit = int(result_limit)
        except (TypeError, ValueError):
            result_limit = None

    page = data.get("page", 1)
    page_size = data.get("page_size", 25)
    try:
        page = max(1, int(page))
        page_size = max(1, min(500, int(page_size)))
    except (TypeError, ValueError):
        page, page_size = 1, 25

    return QueryPlan(
        entity=entity,
        filters=filters,
        sort=sort,
        joins=joins,
        aggregations=aggregations,
        group_by=[str(g) for g in (data.get("group_by") or []) if g],
        result_limit=result_limit,
        page=page,
        page_size=page_size,
        select_fields=[str(f) for f in (data.get("select_fields") or []) if f],
    )
