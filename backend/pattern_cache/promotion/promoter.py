"""
PatternPromoter — writes LLM-synthesised patterns back to *.patterns.yaml files.

Promotion rules:
  confidence >= auto_promote_threshold AND auto_promote_enabled → auto-write to YAML
  confidence >= review_queue_threshold (but < auto_promote) → write to review queue
  confidence < review_queue_threshold → discard

YAML writes are atomic (tmp file + rename). asyncio.Lock serialises concurrent writes.
Pattern IDs use the "<entity_lower>.llm_" prefix for global uniqueness.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import structlog

log = structlog.get_logger(__name__)


@dataclass
class PromotionResult:
    promoted: bool
    queued_for_review: bool
    pattern_id: str | None
    reason: str


class PatternPromoter:
    """
    Writes LLM-synthesised patterns back to *.patterns.yaml files.
    """

    def __init__(
        self,
        skills_dir: Path,
        auto_promote_enabled: bool = True,
        auto_promote_threshold: float = 0.95,
        review_queue_threshold: float = 0.90,
        review_queue_path: Path = Path("./pattern_reviews/"),
        hash_length: int = 16,
        on_promote_callback: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._skills_dir = skills_dir
        self._auto_promote_enabled = auto_promote_enabled
        self._auto_promote_threshold = auto_promote_threshold
        self._review_queue_threshold = review_queue_threshold
        self._review_queue_path = review_queue_path
        self._hash_length = hash_length
        self._on_promote_callback = on_promote_callback
        # Filesystem write lock to prevent concurrent YAML corruption
        self._write_lock = asyncio.Lock()

    async def promote(
        self,
        user_input: str,
        query_plan: "QueryPlan",
        confidence: float,
        entity: str,
        pattern_file_path: Path | None = None,
    ) -> PromotionResult:
        if confidence < self._review_queue_threshold:
            return PromotionResult(False, False, None, "confidence below review threshold")

        pattern_id = self._generate_pattern_id(entity, user_input)
        new_pattern = self._build_pattern_dict(pattern_id, user_input, query_plan)

        if confidence >= self._auto_promote_threshold and self._auto_promote_enabled:
            path = pattern_file_path or self._resolve_pattern_file(entity)
            if path is None:
                return PromotionResult(
                    False, False, None,
                    f"no patterns file found for entity {entity!r}"
                )
            await self._append_to_patterns_file(path, entity, new_pattern)
            log.info("promoter.auto_promoted", pattern_id=pattern_id,
                     entity=entity, confidence=confidence)
            return PromotionResult(True, False, pattern_id, "auto-promoted")
        else:
            await self._write_to_review_queue(entity, user_input, new_pattern, confidence)
            log.info("promoter.queued_for_review", pattern_id=pattern_id,
                     entity=entity, confidence=confidence)
            return PromotionResult(False, True, pattern_id, "queued for operator review")

    def _generate_pattern_id(self, entity: str, user_input: str) -> str:
        """Generate a stable pattern ID from entity + normalised input."""
        import re
        slug = re.sub(r"[^\w\s]", "", user_input.lower())
        slug = "_".join(slug.split()[:5])  # max 5 words
        return f"{entity.lower()}.llm_{slug}"

    def _build_pattern_dict(
        self, pattern_id: str, user_input: str, plan: "QueryPlan"
    ) -> dict:
        """Convert a QueryPlan back to the patterns YAML dict format."""
        import json
        template_data = {
            "filters": [{"field": f.field, "op": f.op, "value": f.value}
                        for f in plan.filters],
            "sort": [{"field": s.field, "dir": s.dir} for s in plan.sort],
            "joins": [{"source_field": j.source_field,
                       "target_entity": j.target_entity,
                       "target_field": j.target_field,
                       "join_type": j.join_type}
                      for j in plan.joins],
            "aggregations": [{"func": a.func, "field": a.field, "alias": a.alias}
                             for a in plan.aggregations],
            "group_by": plan.group_by,
            "result_limit": plan.result_limit,
        }
        return {
            "id": pattern_id,
            "description": f"LLM-synthesised: {user_input[:80]}",
            "triggers": [user_input],
            "query_template": json.dumps(template_data),
            "params": [],
        }

    def _resolve_pattern_file(self, entity: str) -> Path | None:
        """Find the *.patterns.yaml for an entity in skills_dir."""
        for path in self._skills_dir.glob("*.patterns.yaml"):
            stem = path.stem.replace(".patterns", "")
            if stem.lower() == entity.lower():
                return path
        return None

    async def _append_to_patterns_file(
        self, path: Path, entity: str, new_pattern: dict
    ) -> None:
        """
        Append a new pattern to an existing *.patterns.yaml file.
        Acquires write lock, re-reads file to avoid races, checks for ID collision,
        appends pattern, rewrites skill_hash header, writes atomically.
        """
        import yaml
        from backend.pattern_cache.versioning.hasher import PatternHasher

        async with self._write_lock:
            text = path.read_text(encoding="utf-8")
            lines = text.split("\n")
            # Strip existing skill_hash header
            body_lines = lines[1:] if lines[0].startswith("# skill_hash:") else lines
            raw = yaml.safe_load("\n".join(body_lines)) or {}
            raw.setdefault("patterns", [])

            # Check for ID collision — skip if already exists
            existing_ids = {p.get("id") for p in raw["patterns"]}
            if new_pattern["id"] in existing_ids:
                log.warning("promoter.id_collision_skipped", pattern_id=new_pattern["id"])
                return

            raw["patterns"].append(new_pattern)

            # Recompute skill hash
            skill_path = path.parent / path.name.replace(".patterns.yaml", ".skill.yaml")
            new_hash = (
                PatternHasher.compute_skill_hash(skill_path)
                if skill_path.exists()
                else "000000000000000"
            )

            new_text = (
                f"# skill_hash: {new_hash}\n"
                + yaml.dump(raw, default_flow_style=False,
                            sort_keys=False, allow_unicode=True)
            )
            tmp_path = path.with_suffix(".yaml.tmp")
            tmp_path.write_text(new_text, encoding="utf-8")
            tmp_path.replace(path)   # atomic rename

        if self._on_promote_callback is not None:
            self._on_promote_callback(entity, path)

    async def _write_to_review_queue(
        self, entity: str, user_input: str, pattern: dict, confidence: float
    ) -> None:
        """Write a candidate pattern to the review queue directory as a YAML file."""
        import yaml
        import time
        self._review_queue_path.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        review_file = self._review_queue_path / f"{entity.lower()}_{ts}.review.yaml"
        content = {
            "entity": entity,
            "confidence": confidence,
            "user_input": user_input,
            "candidate_pattern": pattern,
            "instructions": "Review and move to skills/<entity>.patterns.yaml to activate.",
        }
        review_file.write_text(
            yaml.dump(content, default_flow_style=False,
                      sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
