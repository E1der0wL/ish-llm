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
    """One lightweight application management entry; runtime loading is explicit."""

    MANAGE_TOOLS = "manage_tools"
    MANAGE_RECORDS = "manage_records"
    MARKDOWN_SUFFIX = ".md"
    JSON_SUFFIX = ".json"
    NO_DATA_CONTRACT = None
    NO_RECORD_MANAGER = "This component does not use the record manager"
    name: str
    tool_prefix: str
    manager: ManagerKind
    import_kind: ImportKind
    data_contract: tuple[str, str] | None = NO_DATA_CONTRACT

    @property
    def operation(self) -> str:
        return (
            self.MANAGE_TOOLS
            if self.manager == ManagerKind.TOOLS
            else self.MANAGE_RECORDS
        )

    @property
    def editor_suffix(self) -> str:
        return (
            self.MARKDOWN_SUFFIX
            if self.import_kind == ImportKind.SKILL
            else self.JSON_SUFFIX
        )

    @property
    def definition_editor(self):
        return self.name in ManagedComponents.DEFINITION_EDITORS

    def data_type(self) -> type:
        """Resolve a fixed catalog entry only inside the backend thread."""
        if self.data_contract is self.NO_DATA_CONTRACT:
            raise ValueError(self.NO_RECORD_MANAGER)
        module, name = self.data_contract
        return getattr(import_module(module), name)


class ManagedComponents:
    """Application management catalog, independent of Backend activation."""

    UNSUPPORTED = "Unsupported component manager: "
    TOOLS = ComponentSpec("tools", "tool", ManagerKind.TOOLS, ImportKind.PACKAGE)
    SKILLS = ComponentSpec(
        "skills",
        "skill",
        ManagerKind.RECORD,
        ImportKind.SKILL,
        ("llm.components.skills.data", "SkillData"),
    )
    MCP = ComponentSpec(
        "mcp",
        "mcp",
        ManagerKind.RECORD,
        ImportKind.JSON,
        ("llm.services.lifecycle.components", "ComponentData"),
    )
    RAG = ComponentSpec(
        "rag",
        "rag",
        ManagerKind.DOCUMENT,
        ImportKind.TEXT,
        ("llm.components.rag.data", "RAGData"),
    )
    AGENTS = ComponentSpec(
        "agents",
        "agent",
        ManagerKind.AGENT,
        ImportKind.JSON,
        ("llm.services.lifecycle.agents", "AgentHandle"),
    )
    WORKFLOWS = ComponentSpec(
        "workflows",
        "workflow",
        ManagerKind.RECORD,
        ImportKind.JSON,
        ("llm.components.workflows.data", "WorkflowData"),
    )
    PROMPTS = ComponentSpec(
        "prompts",
        "prompt",
        ManagerKind.RECORD,
        ImportKind.JSON,
        ("llm.services.lifecycle.components", "ComponentData"),
    )
    MEMORY = ComponentSpec(
        "memory",
        "memory",
        ManagerKind.MEMORY,
        ImportKind.JSON,
        ("llm.components.memory.data", "MemoryData"),
    )
    ALL = (TOOLS, SKILLS, MCP, RAG, AGENTS, WORKFLOWS, PROMPTS, MEMORY)
    DEFINITION_EDITORS = (PROMPTS.name, AGENTS.name, WORKFLOWS.name, MEMORY.name)


COMPONENTS = ManagedComponents.ALL


def component_spec(name: str) -> ComponentSpec:
    for spec in COMPONENTS:
        if spec.name == name:
            return spec
    raise ValueError(ManagedComponents.UNSUPPORTED + name)
