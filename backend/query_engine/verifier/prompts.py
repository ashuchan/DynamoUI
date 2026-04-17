"""Versioned verifier prompts. Keep short and structured."""
from __future__ import annotations

import json

from backend.adapters.base import QueryPlan

VERIFIER_PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """\
You are a query correctness verifier for DynamoUI. Your job is to decide
whether a proposed query plan correctly answers the user's natural-language
request against the given schema. Return JSON matching the schema below.
Respond with ONLY the JSON object. No markdown fences, no commentary.

Response schema:
{
  "verdict": "approve" | "reject" | "approve_with_note",
  "reason": "<one sentence>",
  "llm_plan": <QueryPlan JSON, only if verdict=="reject">,
  "pattern_gap_suggestion": {
    "suggestion_type": "add_trigger" | "new_pattern" | "refine_description",
    "target_pattern_id": "<existing id, only if add_trigger>",
    "proposed_nl_trigger": "<string, only if add_trigger>",
    "proposed_pattern_body": <pattern YAML dict, only if new_pattern>
  }
}

QueryPlan JSON shape (same as the synthesiser uses):
{
  "entity": "<PascalCase entity>",
  "filters": [{"field": "<snake_case>", "op": "<eq|ne|gt|gte|lt|lte|in|like|is_null>", "value": <any>}],
  "sort": [{"field": "<snake_case>", "dir": "<asc|desc>"}],
  "joins": [{"source_field": "...", "target_entity": "...", "target_field": "...", "join_type": "inner|left"}],
  "aggregations": [{"func": "count|sum|avg|min|max", "field": "<snake_case or *>", "alias": "<string>"}],
  "group_by": ["<snake_case>"],
  "result_limit": <int|null>,
  "page": 1,
  "page_size": 25,
  "select_fields": []
}
"""


def _plan_summary(plan: QueryPlan) -> dict:
    return {
        "entity": plan.entity,
        "fields_returned": plan.select_fields or "(all non-sensitive)",
        "filters": [
            {"field": f.field, "op": f.op, "value": f.value} for f in plan.filters
        ],
        "joins": [
            {
                "source_field": j.source_field,
                "target_entity": j.target_entity,
                "target_field": j.target_field,
                "join_type": j.join_type,
            }
            for j in plan.joins
        ],
        "aggregations": [
            {"func": a.func, "field": a.field, "alias": a.alias}
            for a in plan.aggregations
        ],
        "group_by": plan.group_by,
        "result_limit": plan.result_limit,
    }


def build_user_prompt(
    *,
    user_input: str,
    plan: QueryPlan,
    candidate_source: str,
    skill_excerpt: str,
) -> str:
    """Compact user prompt — only the skill context for entities in the plan."""
    payload = {
        "user_intent": user_input,
        "proposed_plan": _plan_summary(plan),
        "plan_source": candidate_source,
        "schema_excerpt": skill_excerpt,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
