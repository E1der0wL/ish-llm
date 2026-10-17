"""Immutable manifest of editable defaults copied only into new projects."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StarterSkill:
    identifier: str
    title: str
    description: str
    filename: str

    def load(self) -> tuple[str, str, str, str]:
        instructions = Path(__file__).with_name("skills").joinpath(self.filename).read_text(encoding="utf-8")
        return self.identifier, self.title, self.description, instructions


WORK_GUIDANCE = Path(__file__).with_name("prompts").joinpath("work-context.md").read_text(encoding="utf-8")
SYSTEM_PROMPT = Path(__file__).with_name("prompts").joinpath("system.md").read_text(encoding="utf-8") + "\n" + WORK_GUIDANCE
TEMPLATE_VERSION = 6
STARTER_SKILLS = (
    StarterSkill("hub-code-change", "Code changes", "Inspect existing behavior and boundaries, then verify the necessary changes.", "coding.md"),
    StarterSkill("hub-diagnose", "Error diagnosis", "Reproduce errors and gather evidence to identify causes and fixes.", "diagnose.md"),
    StarterSkill("hub-document", "Document writing", "Write and review evidence-based documentation for its audience and purpose.", "document.md"),
    StarterSkill("hub-create-rag", "Create RAG documents", "Read source material and register searchable RAG documents.", "create-rag.md"),
    StarterSkill("hub-create-workflow", "Create workflows", "Define and validate graph execution workflows.", "create-workflow.md"),
    StarterSkill("hub-create-skill", "Create skills", "Write and save reusable task instructions.", "create-skill.md"),
    StarterSkill("hub-create-agent", "Create agents", "Define agents with clear responsibilities and tool access.", "create-agent.md"),
)
SKILLS = tuple(skill.load() for skill in STARTER_SKILLS)
