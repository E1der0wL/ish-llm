"""개발자가 제공한 Python Tool 함수와 JSON Schema를 등록하고 호출 인자를 검증한다. 프로젝트의 영속 선택 정보는 ToolComponent가 소유한다.

Explicitly registered async tools; no automatic shell or filesystem access."""

import re
from dataclasses import dataclass, field, replace
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Optional

from jsonschema import Draft202012Validator
from llm.core.model_protocol import ToolDefinition, copy_model, json_copy
from llm.core.tool_contracts import ToolClassification, ToolContract, ToolEffect, ToolIsolation
from llm.core.tool_selection_records import ToolBindingRecord
from llm.core.checkpoint_records import CheckpointRecords
from llm.core.schema import SchemaKeyword
from .bindings import ToolExecutionBinding, ToolHandlerBindings, ToolRegistrationBinding


@dataclass(frozen=True, slots=True)
class Tool:
    INVALID_CONTRACT = "Tool contract must be ToolContract"

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
            raise TypeError(self.INVALID_CONTRACT)


# 런타임 함수와 인자 Schema를 등록한다. 저장된 Tool 선택과 분리된다.
class ToolRegistry:
    NAME_PATTERN = r"[A-Za-z0-9_-]{1,64}"
    INVALID_CLASSIFICATION = "Tool classification must be ToolClassification"
    INVALID_NAME = "Invalid or duplicate tool name"
    INVALID_RETRY_FIELDS = "Tool retry_fields must name declared parameters"
    DURABLE_RETRY_FIELDS = "Durable selected Tool bindings do not support retry_fields; start a new invocation"
    MISMATCHED_DEFINITION = "Tool definition does not match its runtime binding"
    CHANGED_SELECTION = "Selected Tool execution configuration changed; start a new request"
    UNAVAILABLE_HANDLER = "Tool handler is unavailable"
    DUPLICATE_ENABLED = "Duplicate enabled tool"
    UNAVAILABLE_ENABLED = "Enabled tool is unavailable"
    INVALID_ARGUMENTS = "Unknown tool or invalid tool arguments"
    NO_CONSTRAINTS = None

    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.classification is not None and not isinstance(tool.classification, ToolClassification):
            raise TypeError(self.INVALID_CLASSIFICATION)
        if not isinstance(tool.contract, ToolContract):
            raise TypeError(Tool.INVALID_CONTRACT)
        if not re.fullmatch(self.NAME_PATTERN, tool.name) or tool.name in self._tools:
            raise ValueError(self.INVALID_NAME)
        schema = deepcopy(tool.parameters)
        ToolDefinition.require_object_parameters(schema)
        if set(tool.contract.retry_fields) - schema.get(SchemaKeyword.PROPERTIES, {}).keys():
            raise ValueError(self.INVALID_RETRY_FIELDS)
        # A durable selected child cannot change identity through argument repair.
        # A new selection requires a new invocation and its own approval binding.
        if (tool.contract.retry_fields
                and ToolHandlerBindings.declares_selection(tool.handler)):
            raise ValueError(self.DURABLE_RETRY_FIELDS)
        from llm.core.schema import validate_local_refs
        validate_local_refs(schema)
        Draft202012Validator.check_schema(schema)
        definition = copy_model(tool.definition)
        if definition is not None:
            if (not isinstance(definition, ToolDefinition) or definition.name != tool.name
                    or definition.parameters != schema or definition.description != tool.description):
                raise ValueError(self.MISMATCHED_DEFINITION)
        self._tools[tool.name] = Tool(tool.name, tool.description, schema, tool.handler, definition,
                                     contract=tool.contract, classification=tool.classification)

    def contracts(self):
        return {name: ToolRegistrationBinding(tool.contract, tool.classification,
                    ToolHandlerBindings.execution_binding(tool.handler)).to_dict()
                for name, tool in self._tools.items()}

    def execution_binding(self, policy):
        """Shared Tool identity inside each Engine's own execution binding.

        The caller supplies the current scope policy; this observation grants no
        approval and does not replace checks immediately before execution.
        """
        return ToolExecutionBinding([tool.to_dict() for tool in self.definitions()],
                                    self.contracts(), policy).to_dict()

    def validate_selections(self, context):
        """Validate only durably selected invocation bindings, including nested scopes."""
        for key, value in CheckpointRecords.records_or_empty(context.checkpoint).items():
            if not ToolBindingRecord.is_entry(key, value):
                continue
            record = ToolBindingRecord.from_checkpoint(value)
            record.require_key(key)
            selected = record.selection
            handler = self.get(selected.tool).handler
            bind = ToolHandlerBindings.binder(handler)
            if not callable(bind) or bind(selected.arguments, context) != selected.binding:
                raise ValueError(self.CHANGED_SELECTION)
            validate = ToolHandlerBindings.validator(handler)
            if callable(validate):
                validate(selected.arguments, context, request_key=record.request_key,
                         checkpoint_name=record.checkpoint_name)

    def definitions(self, *, constraints=NO_CONSTRAINTS) -> tuple[ToolDefinition, ...]:
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
            raise ValueError(self.UNAVAILABLE_HANDLER) from None
        return Tool(tool.name, tool.description, deepcopy(tool.parameters), tool.handler,
                    copy_model(tool.definition), contract=tool.contract, classification=tool.classification)

    def select(self, names: tuple[str, ...]) -> "ToolRegistry":
        if len(set(names)) != len(names):
            raise ValueError(self.DUPLICATE_ENABLED)
        try:
            return ToolRegistry(tuple(self._tools[name] for name in names))
        except KeyError:
            raise ValueError(self.UNAVAILABLE_ENABLED) from None

    def extend(self, other: "ToolRegistry") -> None:
        for tool in other._tools.values():
            self.register(tool)

    def prepare(self, name: str, arguments: dict, *, constraints=NO_CONSTRAINTS) -> tuple[Tool, dict[str, Any]]:
        """Validate before executing any tool in a returned batch."""
        try:
            tool = self._tools[name]
            values = json_copy(arguments)
            from .constraints import constrained_arguments
            values = constrained_arguments(tool.parameters, values, (constraints or {}).get(name, {}))
        except Exception:
            raise ValueError(self.INVALID_ARGUMENTS) from None
        return tool, values
