"""Immutable Hub management catalog; importing it loads neither PTK nor llm."""

from dataclasses import dataclass
from importlib import import_module
from typing import Literal


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    name: str
    singular: str
    manager: Literal["tools", "record", "document", "agent"]
    import_kind: Literal["package", "json", "text", "skill"]
    data_contract: tuple[str, str] | None = None
    alias_order: int = 0

    @property
    def commands(self) -> tuple[str, ...]:
        return (self.name,) if self.name == self.singular else (self.name, self.singular)

    @property
    def operation(self) -> str:
        return "manage_tools" if self.manager == "tools" else "manage_records"

    def data_type(self) -> type:
        """Resolve a fixed catalog entry only inside the backend thread."""
        if self.data_contract is None:
            raise ValueError("This component does not use the record manager")
        module, name = self.data_contract
        return getattr(import_module(module), name)


COMPONENTS = (
    ComponentSpec("tools", "tool", "tools", "package"),
    ComponentSpec("rag", "rag", "document", "text", ("llm.components.rag.data", "RAGData")),
    ComponentSpec("workflows", "workflow", "record", "json", ("llm.components.workflows.data", "WorkflowData"), alias_order=2),
    ComponentSpec("skills", "skill", "record", "skill", ("llm.components.skills.data", "SkillData"), alias_order=1),
    ComponentSpec("agents", "agent", "agent", "json", ("llm.components.agents.data", "AgentData"), alias_order=3),
)


def component_spec(name: str) -> ComponentSpec:
    for spec in COMPONENTS:
        if spec.name == name:
            return spec
    raise ValueError("Unsupported component manager: " + name)
