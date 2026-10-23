"""MCP 서버 연결 정의를 저장한다. 프로세스 시작과 네트워크 연결은 실행 어댑터의 책임이다."""

from llm.components.tools.registry import ToolRegistry

from llm.components.definitions import DefinitionComponent
from .records import MCPDefinition, MCPConfiguration, MCPRuntime
from .data import MCPData


class MCPComponent(DefinitionComponent):
    """stdio 또는 HTTP 서버의 연결 정보를 JSON으로 관리한다."""

    name = directory = MCPDefinition.CAPABILITY
    capabilities = (MCPDefinition.CAPABILITY, ToolRegistry.CAPABILITY)
    data_class = MCPData
    REVISION = "2"
    schema = MCPDefinition.SCHEMA
    INVALID_CONNECTOR = "MCP connector must be callable"
    INVALID_REVISION = "MCP revision must be nonempty text"

    def __init__(self, *, connector=MCPRuntime.NO_CONNECTOR, revision=REVISION):
        """connector(definition)는 ToolRegistry를 제공하는 async context manager다.

        SDK/호스트별 연결은 개발자가 주입한다. CRUD는 연결하지 않고 명시적 준비,
        Tool 호출 또는 Agent 실행 범위에서 세션을 연다. 원격 Tool 호출은 공통
        ToolExecutor를 거친다. 어댑터를 바꾸면
        revision도 변경하여 이전 Workflow 재개를 차단한다.
        """
        if connector is not MCPRuntime.NO_CONNECTOR and not callable(connector):
            raise TypeError(self.INVALID_CONNECTOR)
        if not isinstance(revision, str) or not revision:
            raise ValueError(self.INVALID_REVISION)
        self.connector, self.revision = connector, revision

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == ToolRegistry.CAPABILITY:
            return data_factory(self.name)._tools()
        result = self.resolve(project, capability)
        if capability == self.name:
            result = MCPRuntime(self.connector, self.revision).include(result)
        return result

    def describe_config(self):
        return MCPConfiguration.schema()
