"""CanvasGenerator — orchestrates ThemeSynthesiser + LayoutSynthesiser +
SkillEnricher + DomainPatternSeeder, writes outputs to canvas-output/{id}/.

Layout (LLD §12):

    canvas-output/{session_id}/
    ├── themes/theme-{name}.css
    ├── skills/{entity}.skill.yaml
    ├── patterns/{entity}.patterns.yaml
    ├── layout.config.yaml
    ├── canvas-session.json     (audit — open question 5 in LLD §18)
    └── README.md
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.canvas.models.intent import CanvasIntent
from backend.canvas.synthesis.domain_pattern_seeder import (
    CanvasDomainPatternSeeder,
)
from backend.canvas.synthesis.layout_synthesiser import LayoutSynthesiser
from backend.canvas.synthesis.skill_enricher import SkillEnricher
from backend.canvas.synthesis.theme_synthesiser import ThemeSynthesiser


@dataclass
class GenerationResult:
    output_dir: Path
    files: list[str]


class CanvasGenerator:
    def __init__(
        self,
        output_dir: Path | str,
        theme: ThemeSynthesiser,
        layout: LayoutSynthesiser,
        enricher: SkillEnricher,
        seeder: CanvasDomainPatternSeeder,
    ) -> None:
        self._root = Path(output_dir)
        self._theme = theme
        self._layout = layout
        self._enricher = enricher
        self._seeder = seeder

    def generate(
        self,
        session_id: str,
        intent: CanvasIntent,
        skill_yamls: dict[str, dict[str, Any]],
        entity_label_resolver=lambda name: name,
    ) -> GenerationResult:
        out = self._root / session_id
        (out / "themes").mkdir(parents=True, exist_ok=True)
        (out / "skills").mkdir(parents=True, exist_ok=True)
        (out / "patterns").mkdir(parents=True, exist_ok=True)
        files: list[str] = []

        # Theme
        theme_css = self._theme.synthesise(intent)
        manifest = self._theme.manifest_for(intent)
        theme_name = intent.custom_theme_name or manifest.name
        theme_path = out / "themes" / f"theme-{theme_name}.css"
        theme_path.write_text(theme_css, encoding="utf-8")
        files.append(str(theme_path.relative_to(out)))

        # Layout
        layout_cfg = self._layout.synthesise(intent)
        layout_path = out / "layout.config.yaml"
        layout_path.write_text(self._layout.to_yaml(layout_cfg), encoding="utf-8")
        files.append(str(layout_path.relative_to(out)))

        # Skills
        for entity_name, skill_yaml in skill_yamls.items():
            enriched = self._enricher.enrich(skill_yaml, intent)
            target = out / "skills" / f"{entity_name}.skill.yaml"
            target.write_text(
                yaml.safe_dump(enriched, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )
            files.append(str(target.relative_to(out)))

        # Patterns
        seeded = self._seeder.seed(intent, entity_label_resolver=entity_label_resolver)
        if seeded:
            for entity_name in {p.entity for p in seeded}:
                target = out / "patterns" / f"{entity_name}.patterns.yaml"
                target.write_text(
                    self._seeder.to_yaml(seeded, entity_name), encoding="utf-8"
                )
                files.append(str(target.relative_to(out)))

        # Audit record
        audit_path = out / "canvas-session.json"
        audit_path.write_text(
            json.dumps(
                {"session_id": session_id, "intent": intent.model_dump()},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        files.append(str(audit_path.relative_to(out)))

        # README
        readme_path = out / "README.md"
        readme_path.write_text(self._readme(intent, files), encoding="utf-8")
        files.append(str(readme_path.relative_to(out)))

        return GenerationResult(output_dir=out, files=files)

    @staticmethod
    def _readme(intent: CanvasIntent, files: list[str]) -> str:
        return (
            "# DynamoUI Canvas Output\n\n"
            f"Domain: **{intent.domain.value if intent.domain else 'n/a'}**  \n"
            f"Mood: **{intent.aesthetic_mood.value if intent.aesthetic_mood else 'n/a'}**  \n"
            f"Profile: **{intent.operation_profile.value if intent.operation_profile else 'n/a'}**\n\n"
            "## Apply\n\n"
            "1. Copy `themes/theme-*.css` to `src/themes/` and set `DYNAMO_THEME_FILE`.\n"
            "2. Diff and merge each `skills/*.skill.yaml` into your `skills/` directory.\n"
            "3. Drop `patterns/*.patterns.yaml` next to the skill files.\n"
            "4. Place `layout.config.yaml` at the project root.\n"
            "5. Run `dynamoui validate` and `python scripts/validate_theme.py` "
            "to confirm everything still passes.\n\n"
            "## Files\n\n" + "\n".join(f"- `{f}`" for f in files) + "\n"
        )

    @staticmethod
    def zip_output(output_dir: Path) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(output_dir))
        return buf.getvalue()
