"""MCP 서버 연결 정의를 저장한다. 프로세스 시작과 네트워크 연결은 실행 어댑터의 책임이다."""

from llm.components.definitions import DefinitionComponent


class MCPComponent(DefinitionComponent):
    """stdio 또는 HTTP 서버의 연결 정보를 JSON으로 관리한다."""

    name = "mcp"
    directory = "mcp"
    capabilities = ("mcp",)
    schema = {
        "type": "object", "required": ["transport"],
        "properties": {
            "transport": {"enum": ["stdio", "streamable_http", "sse"]},
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

    def __init__(self, *, connector=None, revision="1"):
        """connector(definition)는 ToolRegistry를 제공하는 async context manager다.

        SDK/호스트별 연결은 개발자가 주입한다. CRUD는 연결하지 않고 Agent 실행 동안만
        세션을 연다. 원격 Tool 호출은 공통 ToolExecutor를 거친다. 어댑터를 바꾸면
        revision도 변경하여 이전 Workflow 재개를 차단한다.
        """
        if connector is not None and not callable(connector):
            raise TypeError("MCP connector must be callable")
        if not isinstance(revision, str) or not revision:
            raise ValueError("MCP revision must be nonempty text")
        self.connector, self.revision = connector, revision

    def resolve_runtime(self, project, capability, *, data_factory):
        result = self.resolve(project, capability)
        if capability == "mcp":
            result.update(connector=self.connector, revision=self.revision)
        return result
