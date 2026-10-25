"""GraphEngine의 tool 노드를 공통 ToolExecutor에 연결한다."""

from llm.components.tools.registry import ToolRegistry
from llm.core.tool_event_result import ToolEventResult


from contextlib import aclosing
from llm.services.runtime.tools import ToolExecutor, ToolExecutionScope, ToolApprovalRequired
from llm.engines.base import EngineEventType
from .engine import _GraphPause
from .handler_contracts import GraphHandlerHooks
from .tool_definition import GraphToolDefinition
from .node_records import GraphStepMetadata
from .checkpoints import GRAPH_CHECKPOINT_NAME
from llm.components.workflows.bindings import WorkflowBindings


class ToolNode:
    """Workflow inputs 매핑, 정적 arguments 또는 state의 arguments_key를 실행 인자로 쓴다."""

    required_capabilities = (ToolRegistry.CAPABILITY,)
    resumable_container = GraphHandlerHooks.DECLARED
    ABSENT = None

    def checkpoint_engine(self, definition, context):
        handler = context.tools.get(GraphToolDefinition(definition).tool).handler
        return handler if GraphHandlerHooks.resumable(handler) else self.ABSENT

    def additional_capabilities(self, definition, capabilities):
        tools = capabilities.get(ToolRegistry.CAPABILITY)
        if tools is self.ABSENT:
            return ()
        handler = tools.get(GraphToolDefinition(definition).tool).handler
        if not GraphHandlerHooks.resumable(handler):
            return ()
        discover = GraphHandlerHooks.discoverer(handler)
        return tuple(
            dict.fromkeys(
                (*GraphHandlerHooks.required_names(handler), *(discover(capabilities) if discover else ()))
            )
        )

    def __init__(self):
        self.executor = ToolExecutor()

    @staticmethod
    def describe_config():
        return GraphToolDefinition.schema()

    def validate(self, definition, context):
        scope = ToolExecutionScope.require(context)
        context.tools.get(GraphToolDefinition(definition).tool)
        GraphToolDefinition.validate_sources(definition)
        if (
            not GraphToolDefinition(definition).has_arguments_key
            and not WorkflowBindings(definition).has_inputs
        ):
            context.tools.prepare(
                GraphToolDefinition(definition).tool,
                GraphToolDefinition(definition).arguments,
                constraints=scope.argument_constraints,
            )

    async def __call__(self, node):
        scope = ToolExecutionScope.require(node.context)
        definition = node.definition
        if WorkflowBindings(definition).has_inputs:
            arguments = node.inputs
        else:
            arguments = (
                node.state[GraphToolDefinition(definition).arguments_key]
                if GraphToolDefinition(definition).has_arguments_key
                else GraphToolDefinition(definition).arguments
            )
        tool, values = node.context.tools.prepare(
            GraphToolDefinition(definition).tool,
            arguments,
            constraints=scope.argument_constraints,
        )
        result = {}
        # node.decision은 pause_before 확인일 수도 있다. Tool 승인만 공통 helper가
        # 원본 checkpoint의 interaction/action과 연결해 해석하도록 위임한다.
        try:
            async with aclosing(
                node.context.execute_tool(
                    tool,
                    values,
                    result=result,
                    executor=self.executor,
                    metadata=GraphStepMetadata.tool(node.node_id, GRAPH_CHECKPOINT_NAME),
                    checkpoint_key=node.checkpoint_key,
                )
            ) as events:
                async for event in events:
                    if event.type == EngineEventType.PAUSED:
                        raise _GraphPause(node.checkpoint_key)
                    await node.emit(event)
        except ToolApprovalRequired as error:
            # ToolExecutor rejects requests raised after effects. Only this trusted
            # pre-effect boundary may declare a resumable waiting node.
            await node.pause(error.request)
        return GraphToolDefinition.result(definition, node.node_id, ToolEventResult(result).value)
