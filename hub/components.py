"""Immutable Hub management catalog; importing it loads neither PTK nor llm."""

from dataclasses import dataclass
from importlib import import_module
from typing import Literal


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    name: str
    tool_prefix: str
    manager: Literal["tools", "record", "document", "agent", "memory"]
    import_kind: Literal["package", "json", "text", "skill"]
    data_contract: tuple[str, str] | None = None

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
    ComponentSpec("skills", "skill", "record", "skill", ("llm.components.skills.data", "SkillData")),
    ComponentSpec("mcp", "mcp", "record", "json", ("llm.services.lifecycle.components", "ComponentData")),
    ComponentSpec("rag", "rag", "document", "text", ("llm.components.rag.data", "RAGData")),
    ComponentSpec("agents", "agent", "agent", "json", ("llm.services.lifecycle.agents", "AgentHandle")),
    ComponentSpec("workflows", "workflow", "record", "json", ("llm.components.workflows.data", "WorkflowData")),
    ComponentSpec("prompts", "prompt", "record", "json", ("llm.services.lifecycle.components", "ComponentData")),
    ComponentSpec("memory", "memory", "memory", "json", ("llm.components.memory.data", "MemoryData")),
)


def component_spec(name: str) -> ComponentSpec:
    for spec in COMPONENTS:
        if spec.name == name:
            return spec
    raise ValueError("Unsupported component manager: " + name)
