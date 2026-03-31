"""
PatternHasher — SHA-256 hash of skill YAML files for pattern versioning.
Every *.patterns.yaml must start with: # skill_hash: <16-char-hash>
Run `dynamoui compile-patterns` in CI to keep hashes current.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class PatternHasher:
    """
    Computes and verifies skill YAML hashes for pattern file integrity.
    Hash length is configurable (default 16 chars) per DYNAMO_CACHE_HASH_LENGTH.
    """

    @staticmethod
    def compute_skill_hash(skill_path: Path, length: int = 16) -> str:
        """Compute a truncated SHA-256 hash of the skill YAML file contents."""
        content = skill_path.read_bytes()
        full_hash = hashlib.sha256(content).hexdigest()
        result = full_hash[:length]
        log.debug(
            "hasher.computed",
            skill_path=str(skill_path),
            hash=result,
            length=length,
        )
        return result

    @staticmethod
    def verify(pattern_file: Path, skill_path: Path, length: int = 16) -> bool:
        """
        Verify that the pattern file's stored hash matches the current skill YAML.
        Pattern file header format: # skill_hash: abc123def456789a
        Returns True if hashes match, False if stale or header missing.
        """
        if not pattern_file.exists():
            log.warning("hasher.verify.pattern_missing", path=str(pattern_file))
            return False

        if not skill_path.exists():
            log.warning("hasher.verify.skill_missing", path=str(skill_path))
            return False

        text = pattern_file.read_text(encoding="utf-8")
        lines = text.split("\n")
        if not lines or not lines[0].startswith("# skill_hash:"):
            log.warning("hasher.verify.no_header", path=str(pattern_file))
            return False

        stored_hash = lines[0].split("skill_hash:")[1].strip()
        current_hash = PatternHasher.compute_skill_hash(skill_path, length)

        if stored_hash != current_hash:
            log.warning(
                "hasher.verify.stale_hash",
                pattern_file=str(pattern_file),
                stored=stored_hash,
                current=current_hash,
            )
            return False

        log.debug("hasher.verify.ok", pattern_file=str(pattern_file), hash=current_hash)
        return True

    @staticmethod
    def read_stored_hash(pattern_file: Path) -> str | None:
        """Extract the stored hash from a pattern file header, or None if missing."""
        if not pattern_file.exists():
            return None
        text = pattern_file.read_text(encoding="utf-8")
        lines = text.split("\n")
        if lines and lines[0].startswith("# skill_hash:"):
            return lines[0].split("skill_hash:")[1].strip()
        return None
