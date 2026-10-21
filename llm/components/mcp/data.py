"""MCP 발견 결과를 명시적으로 준비하고, 저장된 선택만 공통 Tool 경계에 연결한다."""

from copy import deepcopy
from dataclasses import asdict

from llm.components.base import validate_name
from llm.components.tools.registry import Tool, ToolContract, ToolClassification, ToolRegistry
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token, async_method
from llm.services.runtime.tools import ToolExecutionError


def tool_snapshot(tool):
    return {"name": tool.name, "description": tool.description, "parameters": deepcopy(tool.parameters),
            "contract": tool.contract.to_dict(),
            "classification": asdict(tool.classification) if tool.classification is not None else None}


class RemoteTool:
    """호출할 때 연결한다. 승인 대기 중에는 서버 프로세스/네트워크를 열지 않는다."""

    def __init__(self, data, server, snapshot, descriptor):
        self.data, self.server = data, server
        self.snapshot, self.descriptor = deepcopy(snapshot), deepcopy(descriptor)
        self.execution_binding = {"component": "mcp", "server": server, "version": snapshot["version"],
                                  "connector": snapshot["connector"], "tool": revision_token(descriptor)}

    async def __call__(self, arguments):
        definition, version, connector, revision = await self.data._async_call(self.data._connection, self.server)
        if (version, revision) != (self.snapshot["version"], self.snapshot["connector"]):
            raise ToolExecutionError("MCP definition changed; prepare and submit again", effect=ToolExecutionError.NO_EFFECT)
        async with connector(definition) as tools:
            tool = tools.get(self.descriptor["name"])
            if tool_snapshot(tool) != self.descriptor:
                raise ToolExecutionError("MCP Tool contract changed; prepare and submit again", effect=ToolExecutionError.NO_EFFECT)
            return await tool.handler(arguments)


class MCPData(ComponentData):
    @workspace_locked
    def _connection(self, identifier):
        project, component = self._current()
        if component.connector is None:
            raise ValueError("MCP connector is not configured")
        definition = component.load(project, identifier)
        return definition, revision_token(definition), component.connector, component.revision

    def _catalog_path(self, project, component, identifier):
        return component._checked(component.root(project) / "catalogs" / (validate_name(identifier) + ".json"))

    @workspace_locked
    def _publish_catalog(self, identifier, version, revision, descriptors):
        project, component = self._current()
        if revision_token(component.load(project, identifier)) != version or component.revision != revision:
            raise ValueError("MCP definition changed during discovery")
        value = {"version": version, "connector": revision, "tools": descriptors}
        atomic_json(self._catalog_path(project, component, identifier), value)
        return deepcopy(value)

    @workspace_locked
    def _catalog(self, identifier):
        project, component = self._current()
        version = revision_token(component.load(project, identifier))
        path = self._catalog_path(project, component, identifier)
        if not path.exists():
            raise ValueError(f"MCP {identifier}: call aprepare before enabling Tools")
        value = read_json(path)
        if (value["version"], value["connector"]) != (version, component.revision):
            raise ValueError(f"MCP {identifier}: discovery is stale; call aprepare again")
        return value

    @workspace_locked
    def _tools(self):
        project, component = self._current()
        result = ToolRegistry()
        for alias, selection in component.get_config(project).get("config", {}).get("tools", {}).items():
            snapshot = self._catalog(selection["server"])
            descriptor = next((item for item in snapshot["tools"] if item["name"] == selection["tool"]), None)
            if descriptor is None:
                raise ValueError(f"MCP Tool is unavailable: {alias}")
            contract = ToolContract.from_dict(descriptor["contract"])
            classification = descriptor["classification"]
            result.register(Tool(alias, descriptor["description"], descriptor["parameters"],
                RemoteTool(self, selection["server"], snapshot, descriptor), contract=contract,
                classification=ToolClassification(**classification) if classification is not None else None))
        return result

    async def aprepare(self, identifier):
        """서버에 연결해 Tool 목록을 갱신한다. Tool은 실행하지 않으며 연결은 반드시 닫는다."""
        definition, version, connector, revision = await self._async_call(self._connection, identifier)
        async with connector(definition) as tools:
            if not isinstance(tools, ToolRegistry):
                raise TypeError("MCP connector must yield ToolRegistry")
            descriptors = [tool_snapshot(tools.get(name)) for name in tools.names()]
        return await self._async_call(self._publish_catalog, identifier, version, revision, descriptors)

    @workspace_locked
    def list_tools(self, identifier):
        """네트워크 없이 검증된 발견 스냅샷을 읽는다. 변경된 연결 정의는 재준비해야 한다."""
        return deepcopy(self._catalog(identifier)["tools"])

    alist_tools = async_method(list_tools)
