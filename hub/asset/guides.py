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
CONVERSATION_FILE = "conversation.md"
PROMPT_ENCODING = "utf-8"
PROMPT_SEPARATOR = "\n"
TEMPLATE_VERSION = 9
def _prompt(name):
    from ..errors.startup import StartupLoadError
    from ..locales.keys import MessageKey
    path = ASSET_ROOT / PROMPTS_DIRECTORY / name
    try:
        return path.read_text(encoding=PROMPT_ENCODING)
    except (OSError, ValueError) as error:
        raise StartupLoadError(error, path, hint=MessageKey.STARTUP_INSTALL_HINT) from error


WORK_GUIDANCE = _prompt(WORK_CONTEXT_FILE)
WORK_CYCLE = _prompt(WORK_CYCLE_FILE)
SYSTEM_PROMPT = _prompt(SYSTEM_FILE) + PROMPT_SEPARATOR + WORK_GUIDANCE
CONVERSATION_PROMPT = _prompt(CONVERSATION_FILE)


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


STARTER_SKILL_NAMES = (
    "hub-code-change", "hub-code-review", "hub-create-agent", "hub-create-rag",
    "hub-create-skill", "hub-create-workflow", "hub-data-analysis", "hub-diagnose", "hub-document",
)
STARTER_SKILLS = tuple(StarterSkill(ASSET_ROOT / SKILLS_DIRECTORY / name) for name in STARTER_SKILL_NAMES)


def load_skills():
    """Read packaged skills explicitly, never while importing the Hub UI."""
    return tuple(skill.load() for skill in STARTER_SKILLS)


def validate_starter_skills():
    from ..errors.startup import StartupLoadError
    from ..locales.keys import MessageKey
    for skill in STARTER_SKILLS:
        try:
            skill.load()
        except (OSError, ValueError, TypeError) as error:
            raise StartupLoadError(error, skill.directory / SkillDocument.FILENAME,
                                   hint=MessageKey.STARTUP_INSTALL_HINT) from error
