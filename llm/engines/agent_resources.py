"""Select and connect only declared Agent resources; values and execution stay separate."""

from copy import deepcopy
from dataclasses import dataclass

from llm.core.contracts import JsonValue
from llm.core.definition_snapshot import DefinitionSnapshot
from llm.core.models import RunErrorCode
from llm.core.policies import ToolPolicyValues
from llm.components.skills.records import SkillRecord
from llm.components.prompts.records import PromptDefinition
from llm.components.mcp.records import MCPDefinition, MCPRuntime
from llm.components.rag.identity import RAGIdentity, RAGToolName
from llm.components.tools.registry import Tool, ToolRegistry
from llm.policies import ExecutionLimitError
from llm.services.infrastructure.storage import revision_token


@dataclass(frozen=True, slots=True)
class SelectedAgentDefinition:
    """A runtime source and its selected definition; connector handles are not JSON."""

    source: dict
    definition: dict


@dataclass(frozen=True, slots=True)
class AgentMCPResource(JsonValue):
    definition: dict
    aliases: dict[str, str]
    revision: str


@dataclass(frozen=True, slots=True)
class AgentResources(JsonValue):
    NO_PROMPT = None
    NO_PROMPT_REVISION = None

    skills: dict[str, dict]
    skill_revisions: dict[str, str]
    prompt: dict | None
    prompt_revision: str | None
    mcp: dict[str, AgentMCPResource]
    rag: bool

    @classmethod
    def create(cls, skills, prompt, mcp, rag):
        return cls(skills, {name: revision_token(value) for name, value in skills.items()}, prompt,
                   revision_token(prompt) if prompt is not cls.NO_PROMPT else cls.NO_PROMPT_REVISION, mcp, bool(rag))

    @property
    def aliases(self):
        return [alias for server in self.mcp.values() for alias in server.aliases]


class AgentResourceResolver:
    NO_PARENT = None
    SINGLE_DEFINITION = 1
    UNIQUE_SELECTION = "{capability}/{identifier} must resolve to exactly one definition"
    REQUIRED_RAG = "Agent RAG resource requires the rag component"
    DUPLICATE_ALIAS = "Agent MCP Tool aliases must be unique"
    WIDENED_TOOLS = "Child Tool selection widens its parent"
    INVALID_MCP = "MCP connector must provide a ToolRegistry"
    PROMPT_MESSAGE = "{role}: {content}"
    SKILL_MESSAGE = "Skill {name}:\n{instructions}"

    @classmethod
    def record(cls, capabilities, capability, identifier):
        candidates = [SelectedAgentDefinition(source, DefinitionSnapshot.records_from(source)[identifier])
                      for source in capabilities.get(capability, ()) if identifier in DefinitionSnapshot.records_from(source)]
        if len(candidates) != cls.SINGLE_DEFINITION:
            raise ValueError(cls.UNIQUE_SELECTION.format(capability=capability, identifier=identifier))
        return candidates[0]

    @staticmethod
    def capabilities(profile):
        resources = profile.resources
        return tuple(name for selected, name in ((resources.skills, SkillRecord.CAPABILITY),
            (resources.mcp, MCPDefinition.CAPABILITY), (resources.rag, RAGIdentity.CAPABILITY),
            (resources.prompt, PromptDefinition.CAPABILITY)) if selected)

    @classmethod
    def select(cls, profile, context):
        selection = profile.resources
        skills = {name: deepcopy(cls.record(context.capabilities, SkillRecord.CAPABILITY, name).definition)
                  for name in selection.skills}
        prompt = (deepcopy(cls.record(context.capabilities, PromptDefinition.CAPABILITY, selection.prompt).definition)
                  if selection.prompt else AgentResources.NO_PROMPT)
        mcp = {}
        for server, aliases in selection.mcp.items():
            selected = cls.record(context.capabilities, MCPDefinition.CAPABILITY, server)
            runtime = MCPRuntime.selected(selected.source, server)
            mcp[server] = AgentMCPResource(selected.definition, aliases, runtime.revision)
        names = list(profile.tools)
        if selection.rag:
            if not context.capabilities.get(RAGIdentity.CAPABILITY):
                raise ValueError(cls.REQUIRED_RAG)
            if RAGToolName.SEARCH.value not in names:
                names.append(RAGToolName.SEARCH.value)
        tools = context.tools.select(tuple(names))
        aliases = [alias for server in mcp.values() for alias in server.aliases]
        if len(set(aliases)) != len(aliases) or set(aliases) & set(names):
            raise ValueError(cls.DUPLICATE_ALIAS)
        parent = context.tool_scope
        if parent is not cls.NO_PARENT and not ToolPolicyValues.permits_selection(parent.allowed_tools, [*names, *aliases]):
            raise ExecutionLimitError(RunErrorCode.TOOL_DENIED, cls.WIDENED_TOOLS)
        return tools, AgentResources.create(skills, prompt, mcp, selection.rag)

    @classmethod
    async def connect(cls, resources, context, tools, stack):
        for server, resource in resources.mcp.items():
            selected = cls.record(context.capabilities, MCPDefinition.CAPABILITY, server)
            remote = await stack.enter_async_context(MCPRuntime.selected(selected.source, server).connector(deepcopy(resource.definition)))
            if not isinstance(remote, ToolRegistry):
                raise TypeError(cls.INVALID_MCP)
            for alias, name in resource.aliases.items():
                tool = remote.get(name)
                tools.register(Tool(alias, tool.description, tool.parameters, tool.handler,
                                    contract=tool.contract, classification=tool.classification))

    @classmethod
    def compose(cls, engine, resources, context):
        if resources.skills or resources.prompt:
            engine = engine.with_instructions(context,
                before=tuple(cls.PROMPT_MESSAGE.format(role=m.role, content=m.content)
                             for m in PromptDefinition.message_values(resources.prompt)) if resources.prompt else (),
                after=tuple(cls.SKILL_MESSAGE.format(name=name, instructions=SkillRecord.instructions(skill))
                            for name, skill in resources.skills.items()))
        return engine
