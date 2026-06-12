"""Skill discovery and parsing.

A skill is a folder under the Documents ``skills/`` area containing a ``SKILL.md``
file (Claude-style). The file may have a YAML front-matter block with ``name`` and
``description``; otherwise the first Markdown heading and paragraph are used. Any
other files in the folder are treated as **referenced scripts** the skill may call.

Malformed skills (missing/empty ``SKILL.md`` or no resolvable name) are reported as
invalid and never offered to the agent (FR-017).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SkillInfo:
    """Parsed metadata for one skill folder."""

    name: str
    description: str
    source_path: str
    instructions: str
    scripts: list[str] = field(default_factory=list)
    valid: bool = True
    error: str | None = None


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` YAML front-matter block from the body."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                if isinstance(meta, dict):
                    return meta, parts[2].strip()
            except yaml.YAMLError:
                pass
    return {}, text.strip()


def _first_heading(body: str) -> str:
    """Return the text of the first Markdown ``#`` heading, if any."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def load_skill(folder: Path) -> SkillInfo:
    """Parse a single skill folder into a :class:`SkillInfo` (valid or invalid)."""
    skill_md = folder / "SKILL.md"
    if not skill_md.exists():
        return SkillInfo(folder.name, "", str(folder), "", valid=False,
                         error="Missing SKILL.md")
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return SkillInfo(folder.name, "", str(folder), "", valid=False,
                         error=f"Unreadable SKILL.md: {exc}")
    if not text.strip():
        return SkillInfo(folder.name, "", str(folder), "", valid=False,
                         error="Empty SKILL.md")

    meta, body = _split_front_matter(text)
    name = (meta.get("name") or _first_heading(body) or folder.name).strip()
    description = (meta.get("description") or "").strip()
    if not name:
        return SkillInfo(folder.name, "", str(folder), "", valid=False,
                         error="No resolvable skill name")

    scripts = sorted(
        p.name for p in folder.iterdir()
        if p.is_file() and p.name != "SKILL.md"
    )
    return SkillInfo(
        name=name, description=description, source_path=str(folder),
        instructions=body, scripts=scripts, valid=True,
    )


def discover_skills(skills_dir: Path) -> list[SkillInfo]:
    """Scan the skills folder; return one :class:`SkillInfo` per subfolder."""
    if not skills_dir.exists():
        return []
    out: list[SkillInfo] = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir():
            out.append(load_skill(child))
    return out
