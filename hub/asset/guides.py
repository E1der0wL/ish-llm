"""Standard Skill directories copied only into new Projects."""

from dataclasses import dataclass
from pathlib import Path
from llm.core.skills import SkillDocument


@dataclass(frozen=True, slots=True)
class StarterSkill:
    directory: Path

    @property
    def identifier(self):
        return self.directory.name

    def load(self) -> tuple[str, str, str, str]:
        document = SkillDocument.parse((self.directory / SkillDocument.FILENAME).read_text(encoding="utf-8"),
                                       identifier=self.identifier)
        return document.name, document.name, document.summary()[SkillDocument.DESCRIPTION], document.text


WORK_GUIDANCE = Path(__file__).with_name("prompts").joinpath("work-context.md").read_text(encoding="utf-8")
SYSTEM_PROMPT = Path(__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8") + "\n" + WORK_GUIDANCE
TEMPLATE_VERSION = 7
STARTER_SKILLS = tuple(StarterSkill(path) for path in sorted(Path(__file__).with_name("skills").iterdir()) if path.is_dir())
SKILLS = tuple(skill.load() for skill in STARTER_SKILLS)
