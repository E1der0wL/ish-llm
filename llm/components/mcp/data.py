"""MCP 발견 결과를 명시적으로 준비하고, 저장된 선택만 공통 Tool 경계에 연결한다."""

from llm.components.tools.registry import Tool, ToolRegistry
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token, async_method
from llm.services.runtime.tools import ToolExecutionError
from .catalogs import MCPCatalogRepository
from .records import MCPCatalog, MCPConnection, MCPConfiguration, MCPToolBinding, MCPToolSnapshot, MCPRuntime


class RemoteTool:
    """호출할 때 연결한다. 승인 대기 중에는 서버 프로세스/네트워크를 열지 않는다."""

    DEFINITION_CHANGED = "MCP definition changed; prepare and submit again"
    CONTRACT_CHANGED = "MCP Tool contract changed; prepare and submit again"

    def __init__(self, data, server, snapshot, descriptor):
        self.data, self.server = data, server
        self.snapshot = MCPCatalog.from_dict(snapshot.to_dict())
        self.descriptor = MCPToolSnapshot.from_dict(descriptor.to_dict())
        self.execution_binding = MCPToolBinding.create(server, snapshot, revision_token(descriptor.to_dict())).to_dict()

    async def __call__(self, arguments):
        connection = await self.data._async_call(self.data._connection, self.server)
        if not self.snapshot.matches(connection.version, connection.revision):
            raise ToolExecutionError(self.DEFINITION_CHANGED, effect=ToolExecutionError.NO_EFFECT)
        async with connection.connector(connection.definition) as tools:
            tool = tools.get(self.descriptor.name)
            if MCPToolSnapshot.from_tool(tool) != self.descriptor:
                raise ToolExecutionError(self.CONTRACT_CHANGED, effect=ToolExecutionError.NO_EFFECT)
            return await tool.handler(arguments)


class MCPData(ComponentData):
    CATALOGS = MCPCatalogRepository()
    CONNECTOR_REQUIRED = "MCP connector is not configured"
    DISCOVERY_CHANGED = "MCP definition changed during discovery"
    STALE = "MCP {identifier}: discovery is stale; call aprepare again"
    UNAVAILABLE_TOOL = "MCP Tool is unavailable: {alias}"
    INVALID_DISCOVERY = "MCP connector must yield ToolRegistry"

    @workspace_locked
    def _connection(self, identifier):
        project, component = self._current()
        if component.connector is MCPRuntime.NO_CONNECTOR:
            raise ValueError(self.CONNECTOR_REQUIRED)
        definition = component.load(project, identifier)
        return MCPConnection(definition, revision_token(definition), component.connector, component.revision)

    @workspace_locked
    def _publish_catalog(self, identifier, version, revision, descriptors):
        project, component = self._current()
        if revision_token(component.load(project, identifier)) != version or component.revision != revision:
            raise ValueError(self.DISCOVERY_CHANGED)
        catalog = MCPCatalog(version, revision, tuple(descriptors))
        self.CATALOGS.write(project, component, identifier, catalog)
        return catalog.to_dict()

    @workspace_locked
    def _catalog(self, identifier):
        project, component = self._current()
        version = revision_token(component.load(project, identifier))
        catalog = self.CATALOGS.read(project, component, identifier)
        if not catalog.matches(version, component.revision):
            raise ValueError(self.STALE.format(identifier=identifier))
        return catalog

    @workspace_locked
    def _tools(self):
        project, component = self._current()
        result = ToolRegistry()
        for alias, selection in MCPConfiguration.selections(component.get_config(project)).items():
            snapshot = self._catalog(selection.server)
            descriptor = snapshot.selected(selection.tool)
            if descriptor is MCPCatalog.NO_SELECTION:
                raise ValueError(self.UNAVAILABLE_TOOL.format(alias=alias))
            result.register(Tool(alias, descriptor.description, descriptor.parameters,
                RemoteTool(self, selection.server, snapshot, descriptor), contract=descriptor.execution_contract,
                classification=descriptor.execution_classification))
        return result

    async def aprepare(self, identifier):
        """서버에 연결해 Tool 목록을 갱신한다. Tool은 실행하지 않으며 연결은 반드시 닫는다."""
        connection = await self._async_call(self._connection, identifier)
        async with connection.connector(connection.definition) as tools:
            if not isinstance(tools, ToolRegistry):
                raise TypeError(self.INVALID_DISCOVERY)
            descriptors = [MCPToolSnapshot.from_tool(tools.get(name)) for name in tools.names()]
        return await self._async_call(self._publish_catalog, identifier, connection.version, connection.revision, descriptors)

    @workspace_locked
    def list_tools(self, identifier):
        """네트워크 없이 검증된 발견 스냅샷을 읽는다. 변경된 연결 정의는 재준비해야 한다."""
        return self._catalog(identifier).tool_records()

    alist_tools = async_method(list_tools)
