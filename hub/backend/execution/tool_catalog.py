"""Hub-only Tool tags; effects and approval always come from native contracts."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from llm.components.rag.identity import RAGToolName
from llm.components.memory.tool_values import MemoryToolDefinition
from llm.components.goals.tool_values import GoalToolContract
from llm.components.refinement.tools import RefinementTool
from llm.components.skills.views import SkillListRequest, SkillReadRequest, SkillResourceRequest
from llm.components.vision.tools import VisionToolDefinition
from llm.components.tools.builtin.catalog_values import PromptReadArguments
from llm.engines.agent_tools import AgentDelegationTool
from llm.components.tools import ToolRegistry
from llm.components.tools.builtin.files import FileTools
from llm.components.tools.builtin.process_values import ShellArguments
from llm.core.tool_contracts import ToolEffect

from .completion_review import TaskCompletion
from ..interactions.question_values import QuestionTool
from ..tools.resource_contracts import RAGAuthoring, AuthoringTool, GoalAuthoring
from ..tools.application import HubApplicationTools
from ..tools.repo_map_values import RepoMapArguments
from ...components import ManagedComponents


class ToolTag(StrEnum):
    READ = "read"
    EFFECTFUL = "effectful"
    UNKNOWN_EFFECT = "unknown_effect"
    APPROVAL = "approval_checked"
    QUESTION = "question"
    SHELL = "shell"
    RAG = "rag"
    FILE = "file"
    REPORT = "completion_report"
    BUILTIN = "builtin"
    TOOL = "tools"
    MEMORY = "memory"
    GOAL = "goals"
    REFINEMENT = "refinement"
    SKILL = "skills"
    AGENT = "agents"
    WORKFLOW = "workflows"
    VISION = "vision"
    MCP = "mcp"
    PROMPT = "prompts"
    APPLICATION = "hub"


def authoring_names(spec):
    return tuple(template.format(prefix=spec.tool_prefix) for template in (
        AuthoringTool.SCHEMA_NAME, AuthoringTool.CREATE_NAME,
        AuthoringTool.LIST_NAME, AuthoringTool.READ_NAME))


@dataclass(frozen=True, slots=True)
class TaggedTool:
    name: str
    tags: frozenset[ToolTag]


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    """Disposable view of an already selected registry; not a permission store."""

    EFFECT_TAGS = MappingProxyType({
        ToolEffect.READ_ONLY: ToolTag.READ,
        ToolEffect.EXTERNAL: ToolTag.EFFECTFUL,
        ToolEffect.UNKNOWN: ToolTag.UNKNOWN_EFFECT,
    })
    DERIVED_TAGS = frozenset((*EFFECT_TAGS.values(), ToolTag.APPROVAL))
    GENERIC_OWNERS = frozenset((ToolTag.BUILTIN, ToolTag.TOOL, ToolTag.MCP))
    GROUPS = MappingProxyType({
        ToolTag.QUESTION: (QuestionTool.NAME,),
        ToolTag.SHELL: (ShellArguments.NAME,),
        ToolTag.RAG: (*tuple(RAGToolName), RAGAuthoring.NAME),
        ToolTag.FILE: (*tuple(declaration.NAME for declaration in FileTools.ARGUMENTS), RepoMapArguments.NAME),
        ToolTag.REPORT: (TaskCompletion.NAME,),
        ToolTag.MEMORY: (MemoryToolDefinition.Name.READ_SOURCE, MemoryToolDefinition.Name.TOOL_RESULT,
            MemoryToolDefinition.Name.SEARCH, MemoryToolDefinition.Name.GET,
            MemoryToolDefinition.Name.CREATE, MemoryToolDefinition.Name.UPDATE, MemoryToolDefinition.Name.DELETE),
        ToolTag.GOAL: (GoalToolContract.LIST_NAME, GoalToolContract.READ_NAME,
            GoalToolContract.PROGRESS_NAME, GoalToolContract.LINK_NAME,
            GoalAuthoring.CREATE_NAME, GoalAuthoring.COMPLETE_NAME),
        ToolTag.REFINEMENT: (RefinementTool.TARGET, RefinementTool.EVIDENCE, RefinementTool.READ,
            RefinementTool.PROPOSE, RefinementTool.APPLY, RefinementTool.ROLLBACK),
        ToolTag.SKILL: (SkillListRequest.NAME, SkillReadRequest.NAME, SkillResourceRequest.NAME,
            *authoring_names(ManagedComponents.SKILLS)),
        ToolTag.AGENT: (AgentDelegationTool.NAME, *authoring_names(ManagedComponents.AGENTS)),
        ToolTag.WORKFLOW: authoring_names(ManagedComponents.WORKFLOWS),
        ToolTag.VISION: (VisionToolDefinition.PREPROCESS, VisionToolDefinition.ANALYZE, VisionToolDefinition.OCR),
        ToolTag.PROMPT: (PromptReadArguments.NAME,),
        ToolTag.APPLICATION: (HubApplicationTools.HELP, HubApplicationTools.PARTICIPANTS,
            HubApplicationTools.SEND, HubApplicationTools.EXPORT, HubApplicationTools.RENAME),
    })
    INVALID_GROUP = "Effect and approval tags are derived from Tool contracts"

    entries: tuple[TaggedTool, ...]

    @classmethod
    def project_groups(cls, project, builtins=None):
        """Observe declared aliases/selections without loading packages or contacting MCP."""
        from llm.components.mcp.records import MCPConfiguration, MCPDefinition
        from llm.components.tools.component import ToolComponent
        from llm.components.tools.builtin.component import BuiltinToolComponent
        from llm.components.tools.selection import ToolSelection
        result = {}
        if ToolComponent.name in project.components:
            config = project.config.parameter_targets.component(ToolComponent.name).config
            result[ToolTag.TOOL] = ToolSelection.read(config, scope=ToolComponent.name).names
        if MCPDefinition.CAPABILITY in project.components:
            parameters = project.config.parameter_targets.component(MCPDefinition.CAPABILITY)
            result[ToolTag.MCP] = tuple(MCPConfiguration.selections(parameters.to_dict()))
        if isinstance(builtins, BuiltinToolComponent) and builtins.name in project.components:
            result[ToolTag.BUILTIN] = builtins.toolkit.registry.names()
        return result

    @classmethod
    def unclassified(cls, entry):
        # Generic package/MCP ownership says where a Tool comes from, not what
        # it does. Keep these Tools discoverable until a functional tag exists.
        return not (entry.tags - cls.DERIVED_TAGS - cls.GENERIC_OWNERS)

    @classmethod
    def from_registry(cls, tools: ToolRegistry, *,
                      extra_groups: Mapping[ToolTag, Sequence[str]] | None = None):
        return cls.from_tools((tools.get(name) for name in tools.names()), extra_groups=extra_groups)

    @classmethod
    def from_tools(cls, tools, *, extra_groups=None):
        """Tag detached definitions already fetched by the catalog owner."""
        by_name: dict[str, set[ToolTag]] = {}
        extra_items = () if extra_groups is None else extra_groups.items()
        for tag, names in (*cls.GROUPS.items(), *extra_items):
            tag = ToolTag(tag)
            if tag in cls.DERIVED_TAGS:
                raise ValueError(cls.INVALID_GROUP)
            for name in names:
                by_name.setdefault(name, set()).add(tag)
        entries = []
        for tool in tools:
            name, contract = tool.name, tool.contract
            tags = {cls.EFFECT_TAGS[contract.effect], *by_name.get(name, ())}
            if contract.approval_required:
                tags.add(ToolTag.APPROVAL)
            entries.append(TaggedTool(name, frozenset(tags)))
        return cls(tuple(entries))
