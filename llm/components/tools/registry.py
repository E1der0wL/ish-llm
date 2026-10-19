"""개발자가 제공한 Python Tool 함수와 JSON Schema를 등록하고 호출 인자를 검증한다. 프로젝트의 영속 선택 정보는 ToolComponent가 소유한다.

Explicitly registered async tools; no automatic shell or filesystem access."""

import json
import re
from dataclasses import asdict, dataclass
from collections.abc import Awaitable, Callable
from copy import deepcopy
from inspect import getattr_static
from typing import Any, Optional

from jsonschema import Draft202012Validator
from llm.core.model_protocol import ToolDefinition
from llm.providers.model_codec import definition_from_legacy, definition_to_legacy


@dataclass(frozen=True, slots=True)
class ToolClassification:
    """신뢰한 Application/구현체가 제공하는 분류. Tool 인자에서 추측하지 않는다."""

    category: str = "tool.execute"
    risk_scheme: Optional[str] = None
    risk: Optional[int] = None

    def __post_init__(self):
        from llm.core.interactions import validate_risk
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("Tool classification category must be nonempty")
        validate_risk(self.risk_scheme, self.risk)


@dataclass(frozen=True, slots=True)
class ToolContract:
    """신뢰한 개발자의 실행 선언. 모델용 함수 정의나 Project 레코드로 권한을 바꿀 수 없다."""
    revision: str = "1"
    effect: str = "unknown"
    isolation: str = "none"
    approval_required: bool = False
    operation_key_required: bool = False
    retry_fields: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("Tool contract revision is required")
        if self.effect not in ("read_only", "external", "unknown") or self.isolation not in ("none", "process", "sandbox"):
            raise ValueError("Invalid Tool effect/isolation contract")
        if type(self.approval_required) is not bool or type(self.operation_key_required) is not bool:
            raise TypeError("Tool contract requirements must be boolean")
        if (not isinstance(self.retry_fields, tuple) or any(not isinstance(k, str) or not k for k in self.retry_fields)
                or len(set(self.retry_fields)) != len(self.retry_fields)):
            raise ValueError("Tool retry_fields must be distinct parameter names")

    def to_dict(self):
        return {**asdict(self), "retry_fields": list(self.retry_fields)}


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    # Optional native definition preserves provider extensions such as strict.
    definition: Optional[dict[str, Any]] = None
    contract: Optional[ToolContract] = None
    classification: Optional[ToolClassification] = None

    def __post_init__(self):
        # The public v1 definition remains an explicit compatibility projection.
        if isinstance(self.definition, ToolDefinition):
            object.__setattr__(self, "definition", definition_to_legacy(self.definition))

    @property
    def model_definition(self):
        return (definition_from_legacy(self.definition) if self.definition is not None else
                ToolDefinition(self.name, self.description, self.parameters))


# 런타임 함수와 인자 Schema를 등록한다. 저장된 Tool 선택과 분리된다.
class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.classification is not None and not isinstance(tool.classification, ToolClassification):
            raise TypeError("Tool classification must be ToolClassification")
        if tool.contract is not None and not isinstance(tool.contract, ToolContract):
            raise TypeError("Tool contract must be ToolContract")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name) or tool.name in self._tools:
            raise ValueError("Invalid or duplicate tool name")
        schema = deepcopy(tool.parameters)
        if schema.get("type") != "object":
            raise ValueError("Tool parameters must describe an object")
        if tool.contract is not None and set(tool.contract.retry_fields) - schema.get("properties", {}).keys():
            raise ValueError("Tool retry_fields must name declared parameters")
        # A durable selected child cannot change identity through argument repair.
        # A new selection requires a new invocation and its own approval binding.
        if (tool.contract is not None and tool.contract.retry_fields
                and getattr_static(tool.handler, "bind_arguments", None) is not None):
            raise ValueError("Durable selected Tool bindings do not support retry_fields; start a new invocation")
        from llm.core.schema import validate_local_refs
        validate_local_refs(schema)
        Draft202012Validator.check_schema(schema)
        definition = deepcopy(tool.definition)
        if definition is not None:
            model = definition_from_legacy(definition)
            if (model.name != tool.name or model.parameters != schema or model.description != tool.description):
                raise ValueError("Tool definition does not match its runtime binding")
            json.dumps(definition, allow_nan=False)
        self._tools[tool.name] = Tool(tool.name, tool.description, schema, tool.handler, definition, tool.contract, tool.classification)

    def contracts(self):
        contracts = {}
        for name, tool in self._tools.items():
            if tool.classification is not None:
                contracts[name] = {"classification": asdict(tool.classification)}
            if tool.contract is not None:
                contracts.setdefault(name, {}).update(tool.contract.to_dict())
                # 신뢰한 실행 adapter의 immutable binding도 승인/재개 fingerprint에 포함한다.
                binding = getattr(tool.handler, "execution_binding", None)
                if binding is not None:
                    json.dumps(binding, allow_nan=False)
                    contracts[name]["execution_binding"] = deepcopy(binding)
        return contracts

    def validate_selections(self, context):
        """Validate only durably selected invocation bindings, including nested scopes."""
        for record in (context.checkpoint or {}).get("records", {}).values():
            if record.get("node_type") != "tool_binding":
                continue
            selected = record["selection"]
            handler = self.get(selected["tool"]).handler
            bind = getattr(handler, "bind_arguments", None)
            if not callable(bind) or bind(deepcopy(selected["arguments"]), context) != selected["binding"]:
                raise ValueError("Selected Tool execution configuration changed; start a new request")
            validate = getattr(handler, "validate_selection", None)
            if callable(validate):
                validate(deepcopy(selected["arguments"]), context, request_key=record["request_key"],
                         checkpoint_name=record["checkpoint_name"])

    def definitions(self, *, constraints=None) -> list[dict[str, Any]]:
        """Stable public/worker/Graph v1 projection; execution uses model_definitions."""
        return [definition_to_legacy(t) for t in self.model_definitions(constraints=constraints)]

    def model_definitions(self, *, constraints=None) -> tuple[ToolDefinition, ...]:
        from dataclasses import replace
        from .constraints import constrained_parameters
        return tuple(replace(tool.model_definition, parameters=constrained_parameters(tool.parameters,
            (constraints or {}).get(tool.name, {}))) for tool in self._tools.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool:
        """Detached definition with the application-owned handler identity."""
        try:
            tool = self._tools[name]
        except KeyError:
            raise ValueError("Tool handler is unavailable") from None
        return Tool(tool.name, tool.description, deepcopy(tool.parameters), tool.handler,
                    deepcopy(tool.definition), tool.contract, tool.classification)

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

    def prepare(self, name: str, arguments: str, *, constraints=None) -> tuple[Tool, dict[str, Any]]:
        """Validate before executing any tool in a returned batch."""
        try:
            tool = self._tools[name]
            def reject_constant(value: str) -> None:
                raise ValueError("Non-finite JSON constant")
            values = deepcopy(arguments) if isinstance(arguments, dict) else json.loads(arguments, parse_constant=reject_constant)
            if not isinstance(values, dict):
                raise ValueError("Expected argument object")
            json.dumps(values, allow_nan=False)
            from .constraints import constrained_arguments
            values = constrained_arguments(tool.parameters, values, (constraints or {}).get(name, {}))
        except Exception:
            raise ValueError("Unknown tool or invalid tool arguments") from None
        return tool, values
