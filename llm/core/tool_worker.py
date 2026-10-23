"""Tool interpreter protocol values. Imports stay stdlib-only until bootstrap finishes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_protocol import ToolDefinition
    from .tool_contracts import ToolContract


class ToolWorkerOperation(StrEnum):
    INSPECT = "inspect"
    EXECUTE = "execute"


class ToolWorkerFailureKind(StrEnum):
    INSPECTION = "inspection"
    EXECUTION = "tool_execution"
    CODED = "coded"
    UNKNOWN = "unknown"


class ToolWorkerProtocolError(ValueError):
    ENVELOPE = "Invalid Tool worker envelope"
    RESULT = "Invalid Tool worker result"
    ERROR = "Invalid Tool worker error"
    CLASSIFICATION = "Invalid Tool failure classification"
    UNKNOWN_ERROR = "Unknown Tool worker error envelope"
    DESCRIPTOR = "Invalid Tool descriptor"
    DESCRIPTOR_FIELDS = "Invalid Tool descriptor fields"
    DESCRIPTOR_CONTRACT = "Invalid Tool descriptor contract"
    UNKNOWN_OPERATION = "Unknown Tool worker operation"
    EXECUTION_FAILED = "Project Tool execution failed"


@dataclass(frozen=True, slots=True)
class ToolWorkerRequest:
    operation: ToolWorkerOperation
    tool_name: str
    project_id: str
    source_path: str
    source_hash: str
    requirements_hash: str
    plugin_lib: str | None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def read(cls, value):
        operation = ToolWorkerOperation(value[cls.operation.__name__])
        target = ToolExecutionRequest if operation == ToolWorkerOperation.EXECUTE else cls
        return target(**{**deepcopy(value), cls.operation.__name__: operation})

    def execute(self, descriptor_hash, arguments, call):
        return ToolExecutionRequest(ToolWorkerOperation.EXECUTE, self.tool_name, self.project_id,
            self.source_path, self.source_hash, self.requirements_hash, self.plugin_lib,
            descriptor_hash, deepcopy(arguments), deepcopy(call))

    def binding(self):
        return ToolSourceBinding(self.source_hash, self.requirements_hash)


@dataclass(frozen=True, slots=True)
class ToolExecutionRequest(ToolWorkerRequest):
    descriptor_hash: str
    arguments: dict
    call: dict | None


@dataclass(frozen=True, slots=True)
class ToolSourceBinding:
    source_hash: str
    requirements_hash: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolWorkerDescriptor:
    name: str
    description: str
    parameters: dict
    definition: ToolDefinition
    contract: ToolContract

    @classmethod
    def from_tool(cls, tool):
        return cls(tool.name, tool.description, deepcopy(tool.parameters), tool.definition, tool.contract)

    def to_dict(self):
        value = asdict(self)
        value[type(self).definition.__name__] = self.definition.to_dict()
        value[type(self).contract.__name__] = self.contract.to_dict()
        return value

    @classmethod
    def read(cls, value, *, name):
        if not isinstance(value, dict) or value.keys() != {f.name for f in fields(cls)} or value[cls.name.__name__] != name:
            raise ToolWorkerProtocolError(ToolWorkerProtocolError.DESCRIPTOR)
        data = cls(**deepcopy(value))
        if (not isinstance(data.description, str) or not data.description.strip()
                or any(not isinstance(item, dict) for item in (data.parameters, data.definition, data.contract))):
            raise ToolWorkerProtocolError(ToolWorkerProtocolError.DESCRIPTOR_FIELDS)
        from .model_protocol import ToolDefinition
        from .tool_contracts import ToolContract
        try:
            return cls(data.name, data.description, data.parameters,
                       ToolDefinition.from_dict(data.definition), ToolContract.from_dict(data.contract))
        except Exception as error:
            raise ToolWorkerProtocolError(ToolWorkerProtocolError.DESCRIPTOR_CONTRACT) from error


@dataclass(frozen=True, slots=True)
class ToolWorkerFailure:
    kind: object
    message: str
    code: object = None
    effect: object = None
    retryable: object = None
    recoverable: object = None

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get(cls.message.__name__), str):
            raise ToolWorkerProtocolError(ToolWorkerProtocolError.ERROR)
        return cls(**{f.name: deepcopy(value.get(f.name)) for f in fields(cls)})

    def to_dict(self):
        cls = type(self)
        selected = {cls.kind.__name__, cls.message.__name__}
        if self.kind in (ToolWorkerFailureKind.CODED, ToolWorkerFailureKind.EXECUTION):
            selected.add(cls.code.__name__)
        if self.kind == ToolWorkerFailureKind.EXECUTION:
            selected.update((cls.effect.__name__, cls.retryable.__name__, cls.recoverable.__name__))
        return {name: value for name, value in asdict(self).items() if name in selected}


@dataclass(frozen=True, slots=True)
class ToolWorkerResponse:
    SUCCESS = True
    FAILURE = False
    ABSENT = None

    ok: bool
    value: object
    error: ToolWorkerFailure | None

    @classmethod
    def success(cls, value):
        return cls(cls.SUCCESS, value, cls.ABSENT)

    @classmethod
    def failure(cls, error):
        return cls(cls.FAILURE, cls.ABSENT, error)

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or type(value.get(cls.ok.__name__)) is not bool:
            raise ToolWorkerProtocolError(ToolWorkerProtocolError.ENVELOPE)
        if value[cls.ok.__name__]:
            if value.keys() != {cls.ok.__name__, cls.value.__name__}:
                raise ToolWorkerProtocolError(ToolWorkerProtocolError.RESULT)
            return cls.success(deepcopy(value[cls.value.__name__]))
        return cls.failure(ToolWorkerFailure.read(value.get(cls.error.__name__)))

    def to_dict(self):
        cls = type(self)
        return {cls.ok.__name__: self.ok,
                **({cls.value.__name__: deepcopy(self.value)} if self.ok else
                   {cls.error.__name__: self.error.to_dict()})}
