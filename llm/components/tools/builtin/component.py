"""호스트 BuiltinTools를 명시적으로 선택한 Project에 연결한다. Python Tool 저장소와 독립적이다."""

from llm.components.base import Component, validate_name
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.infrastructure.storage import revision_token


class BuiltinToolComponent(Component):
    """Toolkit 수명은 호스트의 async with가 소유하고, 선택 목록은 ProjectConfig가 소유한다."""

    capabilities = ("tools",)

    def __init__(self, toolkit, *, name: str):
        self.name = self.directory = validate_name(name)
        self.toolkit = toolkit

    def configuration_schema(self):
        return {"type": "object", "properties": {"enabled": {
            "type": "array", "uniqueItems": True,
            "items": {"type": "string", "enum": list(self.toolkit.registry.names())},
        }}, "additionalProperties": False}

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        if self.toolkit.closed:
            raise RuntimeError("BuiltinTools is closed")
        selected = self.toolkit.registry.select(tuple(self.configuration(project).get("enabled", ())))
        contract = ToolContract(revision=revision_token(self.toolkit._execution_binding))
        # 작업 루트/검증 명령을 바꾸고 옛 승인을 이어서 사용하는 것을 막는다.
        return ToolRegistry(tuple(Tool(tool.name, tool.description, tool.parameters, tool.handler,
                                       definition=tool.definition, contract=tool.contract or contract)
                                  for tool in (selected.get(name) for name in selected.names())))
