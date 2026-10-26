"""Standard Skill directories copied only into new Projects."""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from llm.core.skills import SkillDocument


ASSET_ROOT = Path(__file__).parent
PROMPTS_DIRECTORY = "prompts"
SKILLS_DIRECTORY = "skills"
WORK_CONTEXT_FILE = "work-context.md"
WORK_CYCLE_FILE = "work-cycle.md"
SYSTEM_FILE = "system.md"
PROMPT_ENCODING = "utf-8"
PROMPT_SEPARATOR = "\n"
TEMPLATE_VERSION = 7
WORK_GUIDANCE = (ASSET_ROOT / PROMPTS_DIRECTORY / WORK_CONTEXT_FILE).read_text(encoding=PROMPT_ENCODING)
WORK_CYCLE = (ASSET_ROOT / PROMPTS_DIRECTORY / WORK_CYCLE_FILE).read_text(encoding=PROMPT_ENCODING)
SYSTEM_PROMPT = (ASSET_ROOT / PROMPTS_DIRECTORY / SYSTEM_FILE).read_text(encoding=PROMPT_ENCODING) + PROMPT_SEPARATOR + WORK_GUIDANCE


class StarterSkillValue(NamedTuple):
    identifier: str
    name: str
    description: str
    markdown: str


@dataclass(frozen=True, slots=True)
class StarterSkill:
    directory: Path

    @property
    def identifier(self):
        return self.directory.name

    def load(self) -> StarterSkillValue:
        document = SkillDocument.parse((self.directory / SkillDocument.FILENAME).read_text(encoding=SkillDocument.TEXT_ENCODING),
                                       identifier=self.identifier)
        return StarterSkillValue(document.name, document.name, document.description, document.text)


STARTER_SKILLS = tuple(StarterSkill(path) for path in sorted((ASSET_ROOT / SKILLS_DIRECTORY).iterdir()) if path.is_dir())
SKILLS = tuple(skill.load() for skill in STARTER_SKILLS)
