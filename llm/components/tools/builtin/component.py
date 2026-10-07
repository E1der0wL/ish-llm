"""호스트 BuiltinTools를 명시적으로 선택한 Project에 연결한다. Python Tool 저장소와 독립적이다."""

from dataclasses import asdict, replace

from llm.components.base import Component, validate_name
from llm.core.parameters import ParameterLayout
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.infrastructure.storage import revision_token


class BuiltinToolComponent(Component):
    """Toolkit 수명은 호스트의 async with가 소유하고, 선택 목록은 ProjectConfig가 소유한다."""

    capabilities = ("tools",)
    parameter_layout = ParameterLayout(config=("enabled",))

    def __init__(self, toolkit, *, name: str):
        self.name = self.directory = validate_name(name)
        self.toolkit = toolkit

    def describe_config(self):
        return self.parameter_layout.schema({"type": "object", "properties": {"enabled": {
            "type": "array", "uniqueItems": True,
            "items": {"type": "string", "enum": list(self.toolkit.registry.names())},
        }}, "additionalProperties": False})

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        if self.toolkit.closed:
            raise RuntimeError("BuiltinTools is closed")
        selected = self.toolkit.registry.select(tuple(self._options(project).get("enabled", ())))
        def bound_contract(tool):
            contract = tool.contract or ToolContract()
            # Tool 고유 불변식과 호스트 작업 환경을 모두 기존 revision에 묶는다.
            # 명시 contract가 있어도 작업 루트/명령 변경으로 옛 승인을 재사용할 수 없다.
            return replace(contract, revision=revision_token({
                "environment": self.toolkit._execution_binding, "contract": asdict(contract)}))
        return ToolRegistry(tuple(Tool(tool.name, tool.description, tool.parameters, tool.handler,
                                       definition=tool.definition, contract=bound_contract(tool),
                                       classification=tool.classification)
                                  for tool in (selected.get(name) for name in selected.names())))
