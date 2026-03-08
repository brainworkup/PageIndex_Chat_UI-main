"""
Skill Manager - manages custom agent skills stored as Markdown files.

Skill format (Markdown with YAML-like header):
    ---
    name: Skill Name
    description: Brief description
    enabled: true
    ---
    
    Detailed instructions for the agent...
"""

import os
import re
import json
import uuid
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    content: str
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        header = (
            f"---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"enabled: {'true' if self.enabled else 'false'}\n"
            f"---\n\n"
        )
        return header + self.content

    @staticmethod
    def from_markdown(text: str, skill_id: str) -> "Skill":
        match = re.match(
            r"^---\s*\n(.*?)\n---\s*\n(.*)",
            text, re.DOTALL,
        )
        if not match:
            return Skill(
                skill_id=skill_id,
                name=skill_id,
                description="",
                content=text.strip(),
            )

        header_text = match.group(1)
        body = match.group(2).strip()

        def _get(key: str, default: str = "") -> str:
            m = re.search(rf"^{key}:\s*(.+)$", header_text, re.MULTILINE)
            return m.group(1).strip() if m else default

        return Skill(
            skill_id=skill_id,
            name=_get("name", skill_id),
            description=_get("description"),
            content=body,
            enabled=_get("enabled", "true").lower() == "true",
        )


class SkillManager:
    """File-based skill storage under skills/ directory."""

    def __init__(self, skills_dir: str = SKILLS_DIR):
        self.skills_dir = skills_dir
        os.makedirs(self.skills_dir, exist_ok=True)

    def _path(self, skill_id: str) -> str:
        return os.path.join(self.skills_dir, f"{skill_id}.md")

    def list_skills(self) -> List[Skill]:
        skills = []
        if not os.path.isdir(self.skills_dir):
            return skills
        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".md"):
                continue
            skill_id = fname[:-3]
            path = os.path.join(self.skills_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    skills.append(Skill.from_markdown(f.read(), skill_id))
            except Exception as e:
                logger.warning(f"Failed to load skill {fname}: {e}")
        return skills

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        path = self._path(skill_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return Skill.from_markdown(f.read(), skill_id)

    def save_skill(self, skill: Skill) -> Skill:
        with open(self._path(skill.skill_id), "w", encoding="utf-8") as f:
            f.write(skill.to_markdown())
        return skill

    def create_skill(self, name: str, description: str, content: str,
                     enabled: bool = True) -> Skill:
        skill_id = re.sub(r"[^\w\-]", "_", name.lower())[:40]
        if os.path.exists(self._path(skill_id)):
            skill_id += f"_{uuid.uuid4().hex[:4]}"
        skill = Skill(
            skill_id=skill_id,
            name=name,
            description=description,
            content=content,
            enabled=enabled,
        )
        return self.save_skill(skill)

    def update_skill(self, skill_id: str, **kwargs) -> Optional[Skill]:
        skill = self.get_skill(skill_id)
        if not skill:
            return None
        for key in ("name", "description", "content", "enabled"):
            if key in kwargs:
                setattr(skill, key, kwargs[key])
        return self.save_skill(skill)

    def delete_skill(self, skill_id: str) -> bool:
        path = self._path(skill_id)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def get_active_skills(self) -> List[Skill]:
        return [s for s in self.list_skills() if s.enabled]

    def build_skill_prompt(self) -> str:
        """Build a prompt section from all active skills."""
        active = self.get_active_skills()
        if not active:
            return ""
        parts = [
            "The following custom skills are active. "
            "Follow their instructions when relevant to the user's question:\n"
        ]
        for s in active:
            parts.append(f"### Skill: {s.name}\n{s.description}\n\n{s.content}\n")
        return "\n".join(parts)


skill_manager = SkillManager()
