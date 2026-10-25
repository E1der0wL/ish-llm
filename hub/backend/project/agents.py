"""Hub-owned Agent presets, used only during explicit new Project creation."""

from hub.backend.execution.engine_selection import HubEngineIdentity

import asyncio
from copy import deepcopy
from llm.core.agents import AgentDefinition


DEFAULT_AGENT = "assistant"
TITLE_AGENT = "_hub_title"
UNSPECIFIED_DEFINITION = None


async def initialize(
    backend, project, *, identifier=DEFAULT_AGENT, definition=UNSPECIFIED_DEFINITION
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
    else:
        definition = deepcopy(definition)
    await project.agents.acreate(definition, identifier=identifier)
    from .naming import TITLE_ENGINE

    if TITLE_ENGINE in backend.engines.names():
        await project.agents.acreate(
            AgentDefinition.selected(TITLE_ENGINE), identifier=TITLE_AGENT
        )
