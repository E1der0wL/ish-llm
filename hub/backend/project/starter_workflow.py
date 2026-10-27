"""New-Project coding resources; never runtime authority or open-time migration."""

from copy import deepcopy

from llm.components.workflows import WorkflowComponent
from llm.core.agents import AgentDefinition, AgentMode
from llm.engines.base import agent_mode
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode

from ...asset.coding_workflow import CodingWorkflow
from ...config.defaults import HubDefaults
from ..execution.engine_selection import HubEngineIdentity
from ..execution.environment import underlying_engine


async def initialize(backend, project, worker_id: str) -> None:
    if (WorkflowComponent.name not in project.data.components
            or HubEngineIdentity.GRAPH not in backend.engines.names()
            or worker_id == HubDefaults.PLANNER_AGENT_ID):
        return
    graph = underlying_engine(backend.engines.get(HubEngineIdentity.GRAPH))
    if not isinstance(graph, GraphEngine):
        return
    handler = graph.workflow_handlers().get("agent")
    if not isinstance(handler, AgentNode):
        return
    worker = await project.agents.aload(worker_id)
    engine_name = worker[AgentDefinition.Field.ENGINE]
    if (engine_name not in handler.engines.names()
            or agent_mode(backend.engines.get(engine_name)) != AgentMode.BEHAVIORAL):
        return
    workflows = await project.components.aget(WorkflowComponent.name)
    await workflows.acreate(CodingWorkflow.definition(worker_id), identifier=HubDefaults.CODING_WORKFLOW_ID)
    await project.agents.acreate(deepcopy(HubDefaults.PLANNER_AGENT), identifier=HubDefaults.PLANNER_AGENT_ID)
