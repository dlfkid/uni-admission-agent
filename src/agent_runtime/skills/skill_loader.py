"""SkillLoader — on-demand knowledge injection (s05 pattern).

Scans ``SKILL.md`` files from a knowledge directory, parses YAML frontmatter,
and provides:

- **Layer 1** (cheap): short descriptions for the system prompt.
- **Layer 2** (on-demand): full content returned via ``load_skill`` tool call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


class SkillLoader:
    """Load and serve knowledge skills from ``SKILL.md`` files."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or _DEFAULT_KNOWLEDGE_DIR
        self.skills: dict[str, dict[str, Any]] = {}
        self._scan()

    def _scan(self) -> None:
        if not self.skills_dir.exists():
            logger.warning("[SkillLoader] Knowledge dir not found: %s", self.skills_dir)
            return

        for f in sorted(self.skills_dir.rglob("SKILL.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body}
            logger.debug("[SkillLoader] Loaded knowledge skill: %s", name)

        logger.info("[SkillLoader] Loaded %d knowledge skill(s)", len(self.skills))

    def get_descriptions(self) -> str:
        """Return a compact list of skill names + descriptions for the system prompt."""
        if not self.skills:
            return "(no knowledge skills available)"

        lines: list[str] = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "")
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """Return the full skill content wrapped in XML tags."""
        skill = self.skills.get(name)
        if not skill:
            available = ", ".join(self.skills) or "none"
            return f"Error: Unknown skill '{name}'. Available: {available}"
        return f'<skill name="{name}">\n{skill["body"]}\n</skill>'

    def list_names(self) -> list[str]:
        return list(self.skills)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---`` fenced YAML frontmatter from the markdown body."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    body = parts[2].strip()
    return meta, body
