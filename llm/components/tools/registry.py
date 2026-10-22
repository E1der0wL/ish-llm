"""개발자가 제공한 Python Tool 함수와 JSON Schema를 등록하고 호출 인자를 검증한다. 프로젝트의 영속 선택 정보는 ToolComponent가 소유한다.

Explicitly registered async tools; no automatic shell or filesystem access."""

import json
import re
from dataclasses import asdict, dataclass, field, fields, replace
from enum import StrEnum
from collections.abc import Awaitable, Callable
from copy import deepcopy
from inspect import getattr_static
from typing import Any, Optional

from jsonschema import Draft202012Validator
from llm.core.model_protocol import ToolDefinition, copy_model, json_copy


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


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ToolIsolation(StrEnum):
    NONE = "none"
    PROCESS = "process"
    SANDBOX = "sandbox"


@dataclass(frozen=True, slots=True)
class ToolContract:
    """신뢰한 개발자의 실행 선언. 모델용 함수 정의나 Project 레코드로 권한을 바꿀 수 없다."""
    revision: str
    effect: ToolEffect
    isolation: ToolIsolation
    approval_required: bool
    operation_key_required: bool
    retry_fields: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("Tool contract revision is required")
        object.__setattr__(self, "effect", ToolEffect(self.effect))
        object.__setattr__(self, "isolation", ToolIsolation(self.isolation))
        if type(self.approval_required) is not bool or type(self.operation_key_required) is not bool:
            raise TypeError("Tool contract requirements must be boolean")
        if (not isinstance(self.retry_fields, tuple) or any(not isinstance(k, str) or not k for k in self.retry_fields)
                or len(set(self.retry_fields)) != len(self.retry_fields)):
            raise ValueError("Tool retry_fields must be distinct parameter names")

    def to_dict(self):
        return {**asdict(self), "effect": self.effect.value, "isolation": self.isolation.value,
                "retry_fields": list(self.retry_fields)}

    @classmethod
    def from_dict(cls, value):
        """Read only a complete current execution declaration."""
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Tool contract requires exactly the current fields")
        if not isinstance(value["retry_fields"], list):
            raise TypeError("Stored Tool retry_fields must be an array")
        return cls(**{**value, "retry_fields": tuple(value["retry_fields"])})


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    # Common model description is separate from the trusted execution contract.
    definition: Optional[ToolDefinition] = None
    contract: ToolContract = field(kw_only=True)
    classification: Optional[ToolClassification] = None

    def __post_init__(self):
        if not isinstance(self.contract, ToolContract):
            raise TypeError("Tool contract must be ToolContract")


# 런타임 함수와 인자 Schema를 등록한다. 저장된 Tool 선택과 분리된다.
class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.classification is not None and not isinstance(tool.classification, ToolClassification):
            raise TypeError("Tool classification must be ToolClassification")
        if not isinstance(tool.contract, ToolContract):
            raise TypeError("Tool contract must be ToolContract")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.name) or tool.name in self._tools:
            raise ValueError("Invalid or duplicate tool name")
        schema = deepcopy(tool.parameters)
        if schema.get("type") != "object":
            raise ValueError("Tool parameters must describe an object")
        if set(tool.contract.retry_fields) - schema.get("properties", {}).keys():
            raise ValueError("Tool retry_fields must name declared parameters")
        # A durable selected child cannot change identity through argument repair.
        # A new selection requires a new invocation and its own approval binding.
        if (tool.contract.retry_fields
                and getattr_static(tool.handler, "bind_arguments", None) is not None):
            raise ValueError("Durable selected Tool bindings do not support retry_fields; start a new invocation")
        from llm.core.schema import validate_local_refs
        validate_local_refs(schema)
        Draft202012Validator.check_schema(schema)
        definition = copy_model(tool.definition)
        if definition is not None:
            if (not isinstance(definition, ToolDefinition) or definition.name != tool.name
                    or definition.parameters != schema or definition.description != tool.description):
                raise ValueError("Tool definition does not match its runtime binding")
        self._tools[tool.name] = Tool(tool.name, tool.description, schema, tool.handler, definition,
                                     contract=tool.contract, classification=tool.classification)

    def contracts(self):
        contracts = {}
        for name, tool in self._tools.items():
            if tool.classification is not None:
                contracts[name] = {"classification": asdict(tool.classification)}
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

    def definitions(self, *, constraints=None) -> tuple[ToolDefinition, ...]:
        from .constraints import constrained_parameters
        definitions = (copy_model(tool.definition) if tool.definition is not None else
                       ToolDefinition(tool.name, tool.description, tool.parameters)
                       for tool in self._tools.values())
        return tuple(replace(definition, parameters=constrained_parameters(definition.parameters,
                     (constraints or {}).get(definition.name, {}))) for definition in definitions)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool:
        """Detached definition with the application-owned handler identity."""
        try:
            tool = self._tools[name]
        except KeyError:
            raise ValueError("Tool handler is unavailable") from None
        return Tool(tool.name, tool.description, deepcopy(tool.parameters), tool.handler,
                    copy_model(tool.definition), contract=tool.contract, classification=tool.classification)

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

    def prepare(self, name: str, arguments: dict, *, constraints=None) -> tuple[Tool, dict[str, Any]]:
        """Validate before executing any tool in a returned batch."""
        try:
            tool = self._tools[name]
            values = json_copy(arguments)
            if not isinstance(values, dict):
                raise ValueError("Expected argument object")
            json.dumps(values, allow_nan=False)
            from .constraints import constrained_arguments
            values = constrained_arguments(tool.parameters, values, (constraints or {}).get(name, {}))
        except Exception:
            raise ValueError("Unknown tool or invalid tool arguments") from None
        return tool, values
