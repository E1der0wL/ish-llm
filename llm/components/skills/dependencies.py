"""Skill deletion observations; references remain in their original definitions."""

from dataclasses import dataclass

from llm.core.agent_views import AgentDependency
from llm.core.contracts import JsonValue


@dataclass(frozen=True, slots=True)
class SkillChild(JsonValue):
    skill_id: str
    version: str


@dataclass(frozen=True, slots=True)
class SkillDependencies(JsonValue):
    agents: list[AgentDependency]
    children: list[SkillChild]

    @property
    def has_dependents(self):
        return bool(self.agents or self.children)
