"""개발자가 제공한 Python Tool 함수와 JSON Schema를 등록하고 호출 인자를 검증한다. 프로젝트의 영속 선택 정보는 ToolComponent가 소유한다.

Explicitly registered async tools; no automatic shell or filesystem access."""

import json
import re
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Optional

from jsonschema import Draft202012Validator


@dataclass(frozen=True, slots=True)
class ToolContract:
    """신뢰한 개발자의 실행 선언. 모델용 함수 정의나 Project 레코드로 권한을 바꿀 수 없다."""
    revision: str = "1"
    effect: str = "unknown"
    isolation: str = "none"
    approval_required: bool = False
    operation_key_required: bool = False

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("Tool contract revision is required")
        if self.effect not in ("read_only", "external", "unknown") or self.isolation not in ("none", "process", "sandbox"):
            raise ValueError("Invalid Tool effect/isolation contract")
        if type(self.approval_required) is not bool or type(self.operation_key_required) is not bool:
            raise TypeError("Tool contract requirements must be boolean")


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    # Optional native definition preserves provider extensions such as strict.
    definition: Optional[dict[str, Any]] = None
    contract: Optional[ToolContract] = None


# 런타임 함수와 인자 Schema를 등록한다. 저장된 Tool 선택과 분리된다.
class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.contract is not None and not isinstance(tool.contract, ToolContract):
            raise TypeError("Tool contract must be ToolContract")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name) or tool.name in self._tools:
            raise ValueError("Invalid or duplicate tool name")
        schema = deepcopy(tool.parameters)
        if schema.get("type") != "object":
            raise ValueError("Tool parameters must describe an object")
        # Tool validation must not fetch remote schemas.
        def check_refs(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("$ref", "$dynamicRef") and not str(item).startswith("#"):
                        raise ValueError("Only local schema references are supported")
                    check_refs(item)
            elif isinstance(value, list):
                for item in value:
                    check_refs(item)
        check_refs(schema)
        Draft202012Validator.check_schema(schema)
        definition = deepcopy(tool.definition)
        if definition is not None:
            function = definition.get("function", {})
            if (definition.get("type") != "function" or function.get("name") != tool.name
                    or function.get("parameters") != schema
                    or function.get("description", "") != tool.description):
                raise ValueError("Tool definition does not match its runtime binding")
            json.dumps(definition, allow_nan=False)
        self._tools[tool.name] = Tool(tool.name, tool.description, schema, tool.handler, definition, tool.contract)

    def contracts(self):
        contracts = {}
        for name, tool in self._tools.items():
            if tool.contract is not None:
                contracts[name] = asdict(tool.contract)
                # 신뢰한 실행 adapter의 immutable binding도 승인/재개 fingerprint에 포함한다.
                binding = getattr(tool.handler, "execution_binding", None)
                if binding is not None:
                    json.dumps(binding, allow_nan=False)
                    contracts[name]["execution_binding"] = deepcopy(binding)
        return contracts

    def definitions(self) -> list[dict[str, Any]]:
        return [deepcopy(tool.definition) if tool.definition is not None else {"type": "function", "function": {
            "name": tool.name, "description": tool.description,
            "parameters": deepcopy(tool.parameters),
        }} for tool in self._tools.values()]

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool:
        """Detached definition with the application-owned handler identity."""
        try:
            tool = self._tools[name]
        except KeyError:
            raise ValueError("Tool handler is unavailable") from None
        return Tool(tool.name, tool.description, deepcopy(tool.parameters), tool.handler,
                    deepcopy(tool.definition), tool.contract)

    def select(self, names: tuple[str, ...]) -> "ToolRegistry":
        if len(set(names)) != len(names):
            raise ValueError("Duplicate enabled tool")
        try:
            return ToolRegistry(tuple(self._tools[name] for name in names))
        except KeyError:
            raise ValueError("Enabled tool is unavailable") from None

    def extend(self, other: "ToolRegistry") -> None:
        for tool in other._tools.values():
            self.register(tool)

    def prepare(self, name: str, arguments: str) -> tuple[Tool, dict[str, Any]]:
        """Validate before executing any tool in a returned batch."""
        try:
            tool = self._tools[name]
            def reject_constant(value: str) -> None:
                raise ValueError("Non-finite JSON constant")
            values = json.loads(arguments, parse_constant=reject_constant)
            if not isinstance(values, dict):
                raise ValueError("Expected argument object")
            json.dumps(values, allow_nan=False)
            Draft202012Validator(tool.parameters).validate(values)
        except Exception:
            raise ValueError("Unknown tool or invalid tool arguments") from None
        return tool, values
