"""Hub-owned Agent presets, used only during explicit new Project creation."""

from hub.backend.execution.engine_selection import HubEngineIdentity

import asyncio
from copy import deepcopy
from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentSnapshot


DEFAULT_AGENT = "assistant"
TITLE_AGENT = "_hub_title"
UNSPECIFIED_DEFINITION = None


async def preset_agents(project):
    records = await project.agents.alist()
    return {name: definition for name, definition in records.items()
            if not HubEngineIdentity.internal(name)}


async def apply_agents(project, definitions):
    """Explicit preset operation; retain unrelated existing definitions."""
    for identifier, definition in definitions.items():
        try:
            current = AgentSnapshot.read(await project.agents.asnapshot(identifier))
        except FileNotFoundError:
            await project.agents.acreate(definition, identifier=identifier)
        else:
            if current.definition != definition:
                await project.agents.arevise(identifier, definition,
                                            expected_revision=current.revision)


async def initialize(
    backend, project, *, identifier=DEFAULT_AGENT, definition=UNSPECIFIED_DEFINITION,
    completion=None,
):
    if definition is UNSPECIFIED_DEFINITION:
        from llm.components.tools.resolver import ComponentToolResolver

        manager = backend.project_manager

        def selected_tools():
            with manager.ownership.scope():
                current = manager.access.require(project.data)
                return list(
                    ComponentToolResolver(
                        manager.components,
                        data_factory=manager.component,
                        agents=manager.agents,
                        engines=backend.engines,
                    )
                    .resolve_tools(current)
                    .names()
                )

        definition = AgentDefinition.selected(
            HubEngineIdentity.LOOP, tools=await asyncio.to_thread(selected_tools)
        )
        from llm.components.memory import MemoryComponent
        from llm.components.memory.tool_values import MemoryToolDefinition
        # This reader is bound by RunManager, so creation-time discovery cannot
        # see it yet. Only the new Hub preset selects it; existing scopes stay intact.
        if (MemoryComponent.name in project.data.components and
                MemoryToolDefinition.Name.TOOL_RESULT not in definition[AgentDefinition.Field.TOOLS]):
            definition[AgentDefinition.Field.TOOLS].append(MemoryToolDefinition.Name.TOOL_RESULT)
        from ...asset.guides import SYSTEM_PROMPT
        definition[AgentDefinition.Field.SYSTEM_PROMPT] = SYSTEM_PROMPT
    else:
        definition = deepcopy(definition)
    if completion:
        definition[AgentDefinition.Field.COMPLETION] = {
            **completion, **definition.get(AgentDefinition.Field.COMPLETION, {})}
    await project.agents.acreate(definition, identifier=identifier)
    from .naming import TITLE_ENGINE

    if TITLE_ENGINE in backend.engines.names():
        await project.agents.acreate(
            AgentDefinition.selected(TITLE_ENGINE), identifier=TITLE_AGENT
        )
