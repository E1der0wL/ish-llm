"""Memory Tool execution handlers; declarations and record projections have explicit Owners."""

from .configuration import MemoryProcessingConfig
from .history import MemoryHistory
from .sources import MemorySource
from .tool_values import MemoryToolDefinition, MemoryToolSchemas, MemoryToolResult
from llm.components.tools import Tool, ToolRegistry


READ_CONTRACT = MemoryToolDefinition.READ_CONTRACT
WRITE_CONTRACT = MemoryToolDefinition.WRITE_CONTRACT


def memory_tools(data) -> ToolRegistry:
    """Capture the lifecycle-bound Data API; do not expose storage or Project selection."""
    registry = ToolRegistry()
    schemas = MemoryToolSchemas()

    def writer(operation):
        async def invoke(arguments):
            from llm.services.runtime.tools import current_tool_call
            call = current_tool_call()
            if call is MemoryToolDefinition.NO_CONTEXT or call.project_id != data.project.id or not all(
                    (call.session_id, call.run_id, call.input_message_id, call.step_id)):
                raise ValueError(MemoryToolDefinition.MISSING_WRITE_CONTEXT)
            source = MemorySource.tool(call)
            return await data._async_call(data._tool_write, operation, arguments, source)
        return invoke

    def session_id():
        from llm.services.runtime.tools import current_tool_call
        call = current_tool_call()
        if call is not MemoryToolDefinition.NO_CONTEXT and call.project_id != data.project.id:
            raise ValueError(MemoryToolDefinition.CROSS_PROJECT)
        return call.session_id if call else MemoryToolDefinition.NO_CONTEXT

    async def search(arguments):
        return MemoryToolResult.search(await data.asearch(**arguments, session_id=session_id()))

    async def get(arguments):
        return await data.aload(**arguments, session_id=session_id())

    async def tool_result(arguments):
        owner = session_id()
        if owner is MemoryToolDefinition.NO_CONTEXT:
            raise ValueError(MemoryToolDefinition.MISSING_RESULT_SESSION)
        return await data._async_call(data.history_reader, owner, **arguments)

    async def read_source(arguments):
        owner = session_id()
        if owner is MemoryToolDefinition.NO_CONTEXT:
            raise ValueError(MemoryToolDefinition.MISSING_SOURCE_SESSION)
        return await data.aread_source(owner, **arguments)

    if MemoryProcessingConfig.from_config_section(data.get_config()).has_tool_result_mode:
        registry.register(Tool(MemoryToolDefinition.Name.READ_SOURCE, MemoryToolDefinition.Description.READ_SOURCE,
            schemas.read_source(), read_source, contract=READ_CONTRACT))

    if data.history_reader is not MemoryToolDefinition.NO_HISTORY_READER:
        registry.register(Tool(MemoryToolDefinition.Name.TOOL_RESULT, MemoryToolDefinition.Description.TOOL_RESULT,
            schemas.tool_result(), tool_result, contract=READ_CONTRACT))

    registry.register(Tool(MemoryToolDefinition.Name.SEARCH, MemoryToolDefinition.Description.SEARCH,
        schemas.search(), search, contract=READ_CONTRACT))
    registry.register(Tool(MemoryToolDefinition.Name.GET, MemoryToolDefinition.Description.GET,
        schemas.get(), get, contract=READ_CONTRACT))
    registry.register(Tool(MemoryToolDefinition.Name.CREATE, MemoryToolDefinition.Description.CREATE,
        schemas.create(), writer(MemoryHistory.CREATE), contract=WRITE_CONTRACT))
    registry.register(Tool(MemoryToolDefinition.Name.UPDATE, MemoryToolDefinition.Description.UPDATE,
        schemas.update(), writer(MemoryHistory.UPDATE), contract=WRITE_CONTRACT))
    registry.register(Tool(MemoryToolDefinition.Name.DELETE, MemoryToolDefinition.Description.DELETE,
        schemas.delete(), writer(MemoryHistory.DELETE), contract=WRITE_CONTRACT))
    return registry
