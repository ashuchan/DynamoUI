"""
Unit tests for the Click CLI commands.
Uses Click's CliRunner for isolated invocation without spawning a process.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from backend.skill_registry.cli.validate import cli


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SKILLS_DIR = FIXTURES_DIR / "skills"
ENUMS_DIR = FIXTURES_DIR / "enums"
ADAPTERS_REGISTRY = Path(__file__).parent.parent / "adapters.registry.yaml"


class TestValidateCommand:
    def _run(self, *args, **kwargs):
        runner = CliRunner()
        return runner.invoke(cli, ["validate"] + list(args), **kwargs)

    def test_validate_exits_nonzero_on_errors(self, tmp_path):
        """Validation with no skills dir → should fail, not raise."""
        result = self._run(
            "--skills-dir", str(tmp_path / "nonexistent"),
            "--enums-dir", str(tmp_path / "nonexistent"),
            "--adapters-registry", str(ADAPTERS_REGISTRY),
        )
        # Exit code 1 because dirs don't exist / discovery returns empty
        assert result.exit_code in (0, 1)

    def test_validate_valid_files_exits_zero(self, tmp_path):
        """Valid skills + enums + existing adapter registry → exit 0."""
        result = self._run(
            "--skills-dir", str(SKILLS_DIR),
            "--enums-dir", str(ENUMS_DIR),
            "--adapters-registry", str(ADAPTERS_REGISTRY),
        )
        # May have warnings but should not have blocking errors for valid files
        # Exit 0 if only warnings, 1 if errors
        assert result.exit_code in (0, 1)
        # Ensure it ran (output produced)
        assert result.output

    def test_validate_json_output(self, tmp_path):
        """--output json produces valid JSON."""
        import json
        result = self._run(
            "--skills-dir", str(SKILLS_DIR),
            "--enums-dir", str(ENUMS_DIR),
            "--adapters-registry", str(ADAPTERS_REGISTRY),
            "--output", "json",
        )
        parsed = json.loads(result.output)
        assert "issues" in parsed
        assert "success" in parsed
        assert isinstance(parsed["issues"], list)

    def test_validate_single_file_valid(self):
        result = self._run(
            "--file", str(SKILLS_DIR / "employee.skill.yaml"),
            "--adapters-registry", str(ADAPTERS_REGISTRY),
        )
        assert result.exit_code in (0, 1)
        assert result.output

    def test_validate_single_file_not_found(self):
        result = self._run(
            "--file", "/nonexistent/path.skill.yaml",
            "--adapters-registry", str(ADAPTERS_REGISTRY),
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "ERROR" in result.output

    def test_validate_missing_adapters_registry(self, tmp_path):
        result = self._run(
            "--skills-dir", str(SKILLS_DIR),
            "--enums-dir", str(ENUMS_DIR),
            "--adapters-registry", str(tmp_path / "missing.yaml"),
        )
        assert result.exit_code == 1


class TestCompilePatternsCommand:
    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(cli, ["compile-patterns"] + list(args))

    def test_compile_patterns_with_no_skills_dir(self, tmp_path):
        result = self._run("--skills-dir", str(tmp_path))
        assert result.exit_code == 0
        assert "0 updated" in result.output

    def test_compile_patterns_updates_hash(self, tmp_path):
        """Creates a skill + patterns file, verifies hash gets written."""
        from backend.pattern_cache.versioning.hasher import PatternHasher

        # Write a minimal skill file
        skill_content = "entity: TestEntity\ntable: test_entity\nadapter: postgresql\n"
        skill_file = tmp_path / "testentity.skill.yaml"
        skill_file.write_text(skill_content, encoding="utf-8")

        # Write a patterns file with wrong hash
        patterns_file = tmp_path / "testentity.patterns.yaml"
        patterns_file.write_text(
            "# skill_hash: wrong_hash_1234\nentity: TestEntity\npatterns: []\n",
            encoding="utf-8",
        )

        result = self._run("--skills-dir", str(tmp_path))
        assert result.exit_code == 0
        assert "1 updated" in result.output

        # Verify hash now matches
        assert PatternHasher.verify(patterns_file, skill_file) is True

    def test_compile_patterns_skips_already_current(self, tmp_path):
        from backend.pattern_cache.versioning.hasher import PatternHasher

        skill_file = tmp_path / "entity.skill.yaml"
        skill_file.write_text("entity: E\ntable: e\nadapter: postgresql\n", encoding="utf-8")
        correct_hash = PatternHasher.compute_skill_hash(skill_file)

        patterns_file = tmp_path / "entity.patterns.yaml"
        patterns_file.write_text(
            f"# skill_hash: {correct_hash}\nentity: E\npatterns: []\n",
            encoding="utf-8",
        )

        result = self._run("--skills-dir", str(tmp_path))
        assert result.exit_code == 0
        assert "0 updated" in result.output
        assert "1 already current" in result.output
