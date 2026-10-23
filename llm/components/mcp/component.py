"""MCP 서버 연결 정의를 저장한다. 프로세스 시작과 네트워크 연결은 실행 어댑터의 책임이다."""

from llm.components.tools.registry import ToolRegistry

from llm.components.definitions import DefinitionComponent
from llm.core.schema import metadata_schema, object_schema, implementation_schema, field
from .data import MCPData


class MCPComponent(DefinitionComponent):
    """stdio 또는 HTTP 서버의 연결 정보를 JSON으로 관리한다."""

    name = "mcp"
    directory = "mcp"
    capabilities = ("mcp", ToolRegistry.CAPABILITY)
    data_class = MCPData
    REVISION = "2"
    schema = {
        "type": "object", "required": ["transport"], "additionalProperties": False,
        "properties": {
            "transport": {"enum": ["stdio", "streamable_http", "sse"]},
            "metadata": metadata_schema(),
            "command": {"type": "string", "minLength": 1},
            "args": {"type": "array", "items": {"type": "string"}},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "url": {"type": "string", "pattern": "^https?://[^/\\s]+"},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "allOf": [{
            "if": {"properties": {"transport": {"const": "stdio"}}},
            "then": {"required": ["command"]},
            "else": {"required": ["url"]},
        }],
    }

    def __init__(self, *, connector=None, revision=REVISION):
        """connector(definition)는 ToolRegistry를 제공하는 async context manager다.

        SDK/호스트별 연결은 개발자가 주입한다. CRUD는 연결하지 않고 명시적 준비,
        Tool 호출 또는 Agent 실행 범위에서 세션을 연다. 원격 Tool 호출은 공통
        ToolExecutor를 거친다. 어댑터를 바꾸면
        revision도 변경하여 이전 Workflow 재개를 차단한다.
        """
        if connector is not None and not callable(connector):
            raise TypeError("MCP connector must be callable")
        if not isinstance(revision, str) or not revision:
            raise ValueError("MCP revision must be nonempty text")
        self.connector, self.revision = connector, revision

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == ToolRegistry.CAPABILITY:
            return data_factory(self.name)._tools()
        result = self.resolve(project, capability)
        if capability == "mcp":
            result.update(connector=self.connector, revision=self.revision)
        return result

    def describe_config(self):
        selection = object_schema({"server": field("string", pattern="^[A-Za-z0-9_-]{1,64}$"),
                                   "tool": field("string", minLength=1)}, required=["server", "tool"])
        return implementation_schema(config=object_schema({"tools": object_schema(
            patternProperties={"^[A-Za-z0-9_-]{1,64}$": selection})}))
