"""Model invocation values, independent of SDKs and persisted Run/Message records.

Adapters interpret opaque state and runtime parameters. These values only validate
their common structure; they cannot grant Tool authority or release usage reservations.
"""

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
import math
from typing import ClassVar


def json_copy(value):
    """Detach finite JSON without coercing keys, objects, or cyclic containers."""
    active = set()

    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list) or id(item) in active:
            raise ValueError("Model state requires finite, acyclic JSON")
        active.add(id(item))
        try:
            if isinstance(item, dict):
                if any(type(key) is not str for key in item):
                    raise ValueError("Model state requires text keys")
                children = item.values()
            else:
                children = item
            for child in children:
                check(child)
        finally:
            active.remove(id(item))

    check(value)
    return deepcopy(value)


def _object(value):
    if not isinstance(value, dict):
        raise TypeError("Model object requires a dictionary")
    return json_copy(value)


def _usage(value):
    if value is None:
        return None
    value = _object(value)

    def check(counts):
        for count in counts.values():
            if isinstance(count, dict):
                check(count)
            elif type(count) is not int or count < 0:
                raise ValueError("Model usage requires nonnegative integer counts")

    check(value)
    return value


def _record(cls, value, *, versioned=False):
    value = _object(value)
    expected = {item.name for item in fields(cls)}
    if versioned:
        expected.add("version")
        if type(value.get("version")) is not int or value["version"] != cls.VERSION:
            raise ValueError("Unsupported model record version")
    if value.keys() != expected:
        raise ValueError("Model record requires exactly all current fields")
    if versioned:
        del value["version"]
    return value


class ModelRole(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"
    OPAQUE = "opaque"


class StopReason(StrEnum):
    COMPLETE = "complete"
    TOOLS = "tools"
    OUTPUT_LIMIT = "output_limit"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ToolMode(StrEnum):
    REQUIRED = "required"
    AUTO = "auto"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ContentPart:
    kind: ContentKind
    value: str
    media_type: str | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "kind", ContentKind(self.kind))
        if not isinstance(self.value, str) or (self.media_type is not None and not isinstance(self.media_type, str)):
            raise TypeError("Content and media type must be text")
        object.__setattr__(self, "opaque", _object(self.opaque))

    def to_dict(self):
        return dict(kind=self.kind.value, value=self.value, media_type=self.media_type, opaque=_object(self.opaque))

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A completely decoded proposal, distinct from an approved execution snapshot."""

    id: str
    name: str
    arguments: dict
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (self.id, self.name)):
            raise ValueError("Tool call requires its ID and name")
        object.__setattr__(self, "arguments", _object(self.arguments))
        object.__setattr__(self, "opaque", _object(self.opaque))

    def to_dict(self):
        return dict(id=self.id, name=self.name, arguments=_object(self.arguments), opaque=_object(self.opaque))

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    content: str
    name: str | None = None

    def __post_init__(self):
        if not isinstance(self.call_id, str) or not self.call_id or not isinstance(self.content, str):
            raise ValueError("Tool result requires a call ID and text")
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("Tool result name must be nonempty text")

    def to_dict(self):
        return dict(call_id=self.call_id, content=self.content, name=self.name)

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    strict: bool | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str):
            raise ValueError("Tool definition requires a name and description")
        parameters = _object(self.parameters)
        if parameters.get("type") != "object":
            raise ValueError("Tool parameters must describe an object")
        if self.strict is not None and type(self.strict) is not bool:
            raise TypeError("Tool strictness must be boolean or None")
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "opaque", _object(self.opaque))

    def to_dict(self):
        return dict(name=self.name, description=self.description, parameters=_object(self.parameters),
                    strict=self.strict, opaque=_object(self.opaque))

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ModelMessage:
    VERSION: ClassVar[int] = 1

    role: ModelRole
    content: str | tuple[ContentPart, ...] | None = None
    calls: tuple[ToolCall, ...] = ()
    result: ToolResult | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "role", ModelRole(self.role))
        if self.content is not None and not isinstance(self.content, str) and not (
                isinstance(self.content, tuple) and all(isinstance(part, ContentPart) for part in self.content)):
            raise TypeError("Message content requires text or typed parts")
        if not isinstance(self.calls, tuple) or any(not isinstance(call, ToolCall) for call in self.calls):
            raise TypeError("Message calls require completed ToolCall values")
        if len({call.id for call in self.calls}) != len(self.calls) or (self.calls and self.role != ModelRole.ASSISTANT):
            raise ValueError("Invalid or duplicate Tool calls")
        if self.result is not None and (not isinstance(self.result, ToolResult) or self.role != ModelRole.TOOL or self.calls):
            raise ValueError("Tool result requires its own message")
        if self.role == ModelRole.TOOL and (self.result is None or self.content is not None):
            raise ValueError("Tool message content belongs to its result")
        object.__setattr__(self, "content", copy_model(self.content))
        object.__setattr__(self, "calls", copy_model(self.calls))
        object.__setattr__(self, "opaque", _object(self.opaque))

    @property
    def text(self):
        if self.result is not None:
            return self.result.content
        if isinstance(self.content, tuple):
            return "".join(part.value for part in self.content if part.kind == ContentKind.TEXT)
        return "" if self.content is None else self.content

    def with_text(self, text):
        if self.result is not None:
            return replace(self, result=replace(self.result, content=text))
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError("Text replacement cannot discard multimodal content")
        return replace(self, content=text)

    def to_dict(self):
        return dict(version=self.VERSION, role=self.role.value,
            content=[part.to_dict() for part in self.content] if isinstance(self.content, tuple) else self.content,
            calls=[call.to_dict() for call in self.calls],
            result=self.result.to_dict() if self.result is not None else None, opaque=_object(self.opaque))

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=True)
        if isinstance(value["content"], list):
            value["content"] = tuple(ContentPart.from_dict(part) for part in value["content"])
        if not isinstance(value["calls"], list):
            raise TypeError("Stored Tool calls require an array")
        value["calls"] = tuple(ToolCall.from_dict(call) for call in value["calls"])
        if value["result"] is not None:
            value["result"] = ToolResult.from_dict(value["result"])
        return cls(**value)


@dataclass(slots=True)
class ModelRequest:
    VERSION: ClassVar[int] = 1
    RESERVED_PARAMETERS: ClassVar[frozenset[str]] = frozenset(("messages", "tools"))

    messages: list[ModelMessage] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    tools: tuple[ToolDefinition, ...] = ()
    tool_mode: ToolMode | None = None

    def __post_init__(self):
        if not isinstance(self.messages, list) or any(not isinstance(message, ModelMessage) for message in self.messages):
            raise TypeError("Request requires typed model messages")
        if not isinstance(self.parameters, dict) or any(not isinstance(key, str) for key in self.parameters):
            raise TypeError("Model parameters require text keys")
        if self.RESERVED_PARAMETERS.intersection(self.parameters):
            raise ValueError("Messages and Tools belong in typed request fields")
        if not isinstance(self.tools, tuple) or any(not isinstance(tool, ToolDefinition) for tool in self.tools):
            raise TypeError("Request requires typed Tool definitions")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Duplicate Tool definition")
        if self.tool_mode is not None:
            self.tool_mode = ToolMode(self.tool_mode)
        self.messages = copy_model(self.messages)
        self.parameters = copy_model(self.parameters)
        self.tools = copy_model(self.tools)

    def validate_exchanges(self, *, complete=True):
        pending, seen = set(), set()
        for message in self.messages:
            if message.calls:
                identifiers = {call.id for call in message.calls}
                if pending or seen.intersection(identifiers):
                    raise ValueError("Overlapping or repeated Tool calls")
                pending = identifiers
                seen.update(identifiers)
            elif message.result is not None:
                if message.result.call_id not in pending:
                    raise ValueError("Unmatched or duplicate Tool result")
                pending.remove(message.result.call_id)
            elif pending:
                raise ValueError("Incomplete Tool exchange")
        if pending and complete:
            raise ValueError("Missing Tool results")

    def to_dict(self):
        return dict(version=self.VERSION, messages=[message.to_dict() for message in self.messages],
                    parameters=_object(self.parameters), tools=[tool.to_dict() for tool in self.tools],
                    tool_mode=self.tool_mode.value if self.tool_mode is not None else None)

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=True)
        if not isinstance(value["messages"], list) or not isinstance(value["tools"], list):
            raise TypeError("Stored messages and Tools require arrays")
        value["messages"] = [ModelMessage.from_dict(message) for message in value["messages"]]
        value["tools"] = tuple(ToolDefinition.from_dict(tool) for tool in value["tools"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    VERSION: ClassVar[int] = 1

    message: ModelMessage
    stop: StopReason = StopReason.UNKNOWN
    usage: dict | None = None
    response_id: str | None = None
    model: str | None = None
    raw_finish_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.message, ModelMessage) or self.message.role != ModelRole.ASSISTANT:
            raise ValueError("Model response requires an assistant message")
        object.__setattr__(self, "message", copy_model(self.message))
        object.__setattr__(self, "stop", StopReason(self.stop))
        if any(value is not None and not isinstance(value, str)
               for value in (self.response_id, self.model, self.raw_finish_reason)):
            raise TypeError("Model response identities must be text or None")
        object.__setattr__(self, "usage", _usage(self.usage))

    def to_dict(self):
        return dict(version=self.VERSION, message=self.message.to_dict(), stop=self.stop.value,
                    usage=_usage(self.usage), response_id=self.response_id,
                    model=self.model, raw_finish_reason=self.raw_finish_reason)

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=True)
        value["message"] = ModelMessage.from_dict(value["message"])
        return cls(**value)


def copy_model(value):
    """Detach model/options containers while preserving runtime client identity."""
    if isinstance(value, ModelRequest):
        return ModelRequest(value.messages, value.parameters, value.tools, value.tool_mode)
    if isinstance(value, (ModelMessage, ModelResponse, ContentPart, ToolCall, ToolResult, ToolDefinition)):
        return type(value).from_dict(value.to_dict())
    if isinstance(value, dict):
        return {key: copy_model(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_model(item) for item in value]
    if isinstance(value, tuple):
        return tuple(copy_model(item) for item in value)
    return value
