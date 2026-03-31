"""
Pattern loader — YAML discovery + Pydantic validation for *.patterns.yaml files.
Reuses the skill_registry yaml_loader for parsing; adds hash verification.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from backend.pattern_cache.models.pattern import TriggerEntry
from backend.pattern_cache.versioning.hasher import PatternHasher
from backend.skill_registry.loader.yaml_loader import ParseError, load_patterns
from backend.skill_registry.models.pattern import PatternFile

log = structlog.get_logger(__name__)


class PatternLoader:
    """
    Loads and optionally hash-verifies *.patterns.yaml files.
    Returns TriggerEntry lists suitable for building the TriggerIndex.
    """

    def __init__(
        self,
        enforce_skill_hash: bool = True,
        hash_length: int = 16,
    ) -> None:
        self._enforce_skill_hash = enforce_skill_hash
        self._hash_length = hash_length

    def load_all(
        self,
        skills_dir: Path,
        *,
        extra_dirs: list[Path] | None = None,
    ) -> tuple[list[PatternFile], list[ParseError]]:
        """
        Discover and load all *.patterns.yaml files in skills_dir.
        Returns (valid_pattern_files, errors).
        """
        search_dirs = [skills_dir] + (extra_dirs or [])
        results: list[PatternFile] = []
        errors: list[ParseError] = []

        for search_dir in search_dirs:
            for path in sorted(search_dir.glob("*.patterns.yaml")):
                try:
                    pf = load_patterns(path)
                except ParseError as exc:
                    log.warning(
                        "pattern_loader.parse_error",
                        path=str(path),
                        error=str(exc),
                    )
                    errors.append(exc)
                    continue

                # Hash verification
                if self._enforce_skill_hash:
                    skill_path = path.parent / path.name.replace(".patterns.yaml", ".skill.yaml")
                    if skill_path.exists():
                        if not PatternHasher.verify(path, skill_path, self._hash_length):
                            log.warning(
                                "pattern_loader.stale_hash",
                                pattern_file=str(path),
                                skill_file=str(skill_path),
                            )
                            # Skip stale patterns — forces compile-patterns to be run
                            continue
                    else:
                        log.warning(
                            "pattern_loader.skill_not_found",
                            pattern_file=str(path),
                            expected_skill=str(skill_path),
                        )

                results.append(pf)
                log.debug(
                    "pattern_loader.loaded",
                    entity=pf.entity,
                    patterns=len(pf.patterns),
                    path=str(path),
                )

        log.info(
            "pattern_loader.complete",
            loaded=len(results),
            errors=len(errors),
        )
        return results, errors

    def build_trigger_entries(
        self, pattern_files: list[PatternFile], stopwords: list[str]
    ) -> list[TriggerEntry]:
        """Convert loaded PatternFiles into flat TriggerEntry objects."""
        entries: list[TriggerEntry] = []
        for pf in pattern_files:
            for pattern in pf.patterns:
                for trigger in pattern.triggers:
                    normalised = self._normalise(trigger, stopwords)
                    entries.append(
                        TriggerEntry(
                            trigger_original=trigger,
                            trigger_normalised=normalised,
                            pattern_id=pattern.id,
                            entity=pf.entity,
                        )
                    )
        log.debug("pattern_loader.triggers_built", count=len(entries))
        return entries

    @staticmethod
    def _normalise(text: str, stopwords: list[str]) -> str:
        """
        Lowercase, strip punctuation, remove stopwords.
        Stopwords are stripped before fuzzy matching per the spec.
        """
        import re
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        tokens = text.split()
        tokens = [t for t in tokens if t not in stopwords]
        return " ".join(tokens)
