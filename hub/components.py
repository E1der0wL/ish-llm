"""Immutable Hub management catalog; importing it loads neither PTK nor llm."""

from dataclasses import dataclass
from importlib import import_module
from enum import StrEnum


class ManagerKind(StrEnum):
    TOOLS = "tools"
    RECORD = "record"
    DOCUMENT = "document"
    AGENT = "agent"
    MEMORY = "memory"


class ImportKind(StrEnum):
    PACKAGE = "package"
    JSON = "json"
    TEXT = "text"
    SKILL = "skill"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    name: str
    tool_prefix: str
    manager: ManagerKind
    import_kind: ImportKind
    data_contract: tuple[str, str] | None = None

    @property
    def operation(self) -> str:
        return "manage_tools" if self.manager == ManagerKind.TOOLS else "manage_records"

    @property
    def editor_suffix(self) -> str:
        return ".md" if self.import_kind == ImportKind.SKILL else ".json"

    def data_type(self) -> type:
        """Resolve a fixed catalog entry only inside the backend thread."""
        if self.data_contract is None:
            raise ValueError("This component does not use the record manager")
        module, name = self.data_contract
        return getattr(import_module(module), name)


COMPONENTS = (
    ComponentSpec("tools", "tool", ManagerKind.TOOLS, ImportKind.PACKAGE),
    ComponentSpec("skills", "skill", ManagerKind.RECORD, ImportKind.SKILL, ("llm.components.skills.data", "SkillData")),
    ComponentSpec("mcp", "mcp", ManagerKind.RECORD, ImportKind.JSON, ("llm.services.lifecycle.components", "ComponentData")),
    ComponentSpec("rag", "rag", ManagerKind.DOCUMENT, ImportKind.TEXT, ("llm.components.rag.data", "RAGData")),
    ComponentSpec("agents", "agent", ManagerKind.AGENT, ImportKind.JSON, ("llm.services.lifecycle.agents", "AgentHandle")),
    ComponentSpec("workflows", "workflow", ManagerKind.RECORD, ImportKind.JSON, ("llm.components.workflows.data", "WorkflowData")),
    ComponentSpec("prompts", "prompt", ManagerKind.RECORD, ImportKind.JSON, ("llm.services.lifecycle.components", "ComponentData")),
    ComponentSpec("memory", "memory", ManagerKind.MEMORY, ImportKind.JSON, ("llm.components.memory.data", "MemoryData")),
)


def component_spec(name: str) -> ComponentSpec:
    for spec in COMPONENTS:
        if spec.name == name:
            return spec
    raise ValueError("Unsupported component manager: " + name)
