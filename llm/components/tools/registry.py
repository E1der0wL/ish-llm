"""개발자가 제공한 Python Tool 함수와 JSON Schema를 등록하고 호출 인자를 검증한다. 프로젝트의 영속 선택 정보는 ToolComponent가 소유한다.

Explicitly registered async tools; no automatic shell or filesystem access."""

import json
import re
import threading
from dataclasses import dataclass, field, replace
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Optional

from llm.core.json_data import JsonObject
from llm.core.model_protocol import ToolDefinition, copy_model, json_copy
from llm.core.tool_contracts import ToolClassification, ToolContract, ToolEffect, ToolIsolation
from llm.core.tool_selection_records import ToolBindingRecord
from llm.core.checkpoint_records import CheckpointRecords
from llm.core.schema import SchemaKeyword
from .bindings import ToolExecutionBinding, ToolHandlerBindings, ToolRegistrationBinding


class _DefinitionLock:
    """Serialize sharing/exposure; copies own a fresh, non-persisted lock."""

    def __init__(self):
        self.lock = threading.RLock()

    def __enter__(self):
        self.lock.acquire()

    def __exit__(self, *args):
        self.lock.release()

    def __deepcopy__(self, memo):
        return type(self)()


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
    CAPABILITY = "tools"
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
        # Proofs concern schema syntax only, never arguments, authority or a
        # handler binding. Content keys also detect mutable Tool schema changes.
        # Lifetime/size follows the registry, not a process-wide cache.
        self._schemas: set[str] = set()
        self._shared: set[str] = set()
        self._exposed: set[str] = set()
        self._definition_lock = _DefinitionLock()
        for tool in tools:
            self.register(tool)

    def copy(self, *, memo=None):
        """Copy registration state without copying every unexposed definition.

        prepare() exposes a live Tool, so already exposed entries must be copied.
        Shared entries detach on their first prepare() in either registry.
        """
        with self._definition_lock:
            identities = dict(memo or {})
            shared = self._tools.keys() - self._exposed
            self._shared.update(shared)
            identities.update((id(tool), tool) for name, tool in self._tools.items() if name in shared)
            identities.update((id(tool.handler), tool.handler) for tool in self._tools.values())
            # Python's normal copy protocol still owns subclass dictionaries/slots.
            selected = deepcopy(self, identities)
            selected._shared = set(shared)
            selected._exposed = set()
            return selected

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
        try:
            JsonObject.validate(schema)
            schema_key = json.dumps(schema, allow_nan=False)
        except (TypeError, ValueError):
            # Non-JSON Python extensions retain their existing validation path;
            # they cannot borrow a proof for a JSON representation.
            schema_key = None
        if schema_key is None or schema_key not in self._schemas:
            from llm.core.schema import validate_schema
            validate_schema(schema)
            if schema_key is not None:
                self._schemas.add(schema_key)
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
            record = context.read_tool_binding(value)
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

    def definitions(self, *, names=None, constraints=NO_CONSTRAINTS) -> tuple[ToolDefinition, ...]:
        """Copy only selected definitions without re-registering runtime handlers."""
        from .constraints import constrained_parameters
        from .exposure import ToolExposure
        selected = self.names() if names is None else ToolExposure.validate_names(names, self.names())
        definitions = []
        for name in selected:
            tool = self._tools[name]
            source = tool.definition
            parameters = constrained_parameters(tool.parameters if source is None else source.parameters,
                                                (constraints or {}).get(name, {}))
            # The final ToolDefinition constructor owns detached JSON fields;
            # do not build/copy an intermediate definition only to replace it.
            definitions.append(ToolDefinition(name, tool.description, parameters) if source is None
                               else replace(source, parameters=parameters))
        return tuple(definitions)

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

    def get_handler(self, name: str):
        """Read runtime identity without materializing a model definition."""
        try:
            return self._tools[name].handler
        except KeyError:
            raise ValueError(self.UNAVAILABLE_HANDLER) from None

    def select(self, names: tuple[str, ...]) -> "ToolRegistry":
        if len(set(names)) != len(names):
            raise ValueError(self.DUPLICATE_ENABLED)
        try:
            tools = tuple(self._tools[name] for name in names)
        except KeyError:
            raise ValueError(self.UNAVAILABLE_ENABLED) from None
        return self.derive(tools)

    def derive(self, tools: tuple[Tool, ...]) -> "ToolRegistry":
        """Register new bindings, reusing only unchanged parameter syntax proofs."""
        selected = ToolRegistry()
        selected._schemas.update(self._schemas)
        for tool in tools:
            selected.register(tool)
        return selected

    def extend(self, other: "ToolRegistry") -> None:
        self._schemas.update(other._schemas)
        for tool in other._tools.values():
            self.register(tool)

    def prepare(self, name: str, arguments: dict, *, constraints=NO_CONSTRAINTS) -> tuple[Tool, dict[str, Any]]:
        """Validate before executing any tool in a returned batch."""
        with self._definition_lock:
            try:
                tool = self._tools[name]
                values = json_copy(arguments)
                from .constraints import constrained_arguments
                values = constrained_arguments(tool.parameters, values, (constraints or {}).get(name, {}))
            except Exception:
                raise ValueError(self.INVALID_ARGUMENTS) from None
            if name in self._shared:
                tool = self.get(name)
                self._tools[name] = tool
                self._shared.remove(name)
            self._exposed.add(name)
            return tool, values
