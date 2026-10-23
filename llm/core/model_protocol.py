"""Model invocation values, independent of SDKs and persisted Run/Message records.

Adapters interpret opaque state and runtime parameters. These values only validate
their common structure; they cannot grant Tool authority or release usage reservations.
"""

from copy import deepcopy
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
import math
from typing import ClassVar


class ModelJson:
    INVALID_STATE = "Model state requires finite, acyclic JSON"
    INVALID_KEYS = "Model state requires text keys"
    INVALID_OBJECT = "Model object requires a dictionary"
    INVALID_USAGE = "Model usage requires nonnegative integer counts"
    MINIMUM_COUNT = 0


class ModelRecord:
    VERSION_FIELD = "version"
    VERSIONED = True
    UNVERSIONED = False
    INVALID_VERSION = "Unsupported model record version"
    INVALID_FIELDS = "Model record requires exactly all current fields"


def json_copy(value):
    """Detach finite JSON without coercing keys, objects, or cyclic containers."""
    active = set()

    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list) or id(item) in active:
            raise ValueError(ModelJson.INVALID_STATE)
        active.add(id(item))
        try:
            if isinstance(item, dict):
                if any(type(key) is not str for key in item):
                    raise ValueError(ModelJson.INVALID_KEYS)
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
        raise TypeError(ModelJson.INVALID_OBJECT)
    return json_copy(value)


def _usage(value):
    if value is None:
        return None
    value = _object(value)

    def check(counts):
        for count in counts.values():
            if isinstance(count, dict):
                check(count)
            elif type(count) is not int or count < ModelJson.MINIMUM_COUNT:
                raise ValueError(ModelJson.INVALID_USAGE)

    check(value)
    return value


def _record(cls, value, *, versioned=ModelRecord.UNVERSIONED):
    value = _object(value)
    expected = {item.name for item in fields(cls)}
    if versioned:
        expected.add(ModelRecord.VERSION_FIELD)
        if type(value.get(ModelRecord.VERSION_FIELD)) is not int or value[ModelRecord.VERSION_FIELD] != cls.VERSION:
            raise ValueError(ModelRecord.INVALID_VERSION)
    if value.keys() != expected:
        raise ValueError(ModelRecord.INVALID_FIELDS)
    if versioned:
        del value[ModelRecord.VERSION_FIELD]
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
    INVALID_TEXT: ClassVar[str] = 'Content and media type must be text'

    kind: ContentKind
    value: str
    media_type: str | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, type(self).kind.__name__, ContentKind(self.kind))
        if not isinstance(self.value, str) or (self.media_type is not None and not isinstance(self.media_type, str)):
            raise TypeError(self.INVALID_TEXT)
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))

    def to_dict(self):
        return {type(self).kind.__name__: self.kind.value,
                type(self).value.__name__: self.value,
                type(self).media_type.__name__: self.media_type,
                type(self).opaque.__name__: _object(self.opaque)}

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A completely decoded proposal, distinct from an approved execution snapshot."""

    INVALID_IDENTITY: ClassVar[str] = 'Tool call requires its ID and name'

    id: str
    name: str
    arguments: dict
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (self.id, self.name)):
            raise ValueError(self.INVALID_IDENTITY)
        object.__setattr__(self, type(self).arguments.__name__, _object(self.arguments))
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))

    def to_dict(self):
        return {type(self).id.__name__: self.id,
                type(self).name.__name__: self.name,
                type(self).arguments.__name__: _object(self.arguments),
                type(self).opaque.__name__: _object(self.opaque)}

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolResult:
    INVALID_CONTENT: ClassVar[str] = 'Tool result requires a call ID and text'
    INVALID_NAME: ClassVar[str] = 'Tool result name must be nonempty text'

    call_id: str
    content: str
    name: str | None = None

    def __post_init__(self):
        if not isinstance(self.call_id, str) or not self.call_id or not isinstance(self.content, str):
            raise ValueError(self.INVALID_CONTENT)
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError(self.INVALID_NAME)

    def to_dict(self):
        return {type(self).call_id.__name__: self.call_id,
                type(self).content.__name__: self.content,
                type(self).name.__name__: self.name}

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    INVALID_IDENTITY: ClassVar[str] = 'Tool definition requires a name and description'
    INVALID_STRICT: ClassVar[str] = 'Tool strictness must be boolean or None'

    INVALID_PARAMETERS = "Tool parameters must describe an object"

    name: str
    description: str
    parameters: dict
    strict: bool | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str):
            raise ValueError(self.INVALID_IDENTITY)
        parameters = _object(self.parameters)
        self.require_object_parameters(parameters)
        if self.strict is not None and type(self.strict) is not bool:
            raise TypeError(self.INVALID_STRICT)
        object.__setattr__(self, type(self).parameters.__name__, parameters)
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))

    @classmethod
    def require_object_parameters(cls, parameters):
        from .schema import SchemaKeyword, SchemaType
        if parameters.get(SchemaKeyword.TYPE) != SchemaType.OBJECT:
            raise ValueError(cls.INVALID_PARAMETERS)

    def to_dict(self):
        return {type(self).name.__name__: self.name,
                type(self).description.__name__: self.description,
                type(self).parameters.__name__: _object(self.parameters),
                type(self).strict.__name__: self.strict,
                type(self).opaque.__name__: _object(self.opaque)}

    @classmethod
    def from_dict(cls, value):
        return cls(**_record(cls, value))


@dataclass(frozen=True, slots=True)
class ModelMessage:
    INVALID_CONTENT: ClassVar[str] = 'Message content requires text or typed parts'
    INVALID_CALLS: ClassVar[str] = 'Message calls require completed ToolCall values'
    DUPLICATE_CALLS: ClassVar[str] = 'Invalid or duplicate Tool calls'
    INVALID_RESULT: ClassVar[str] = 'Tool result requires its own message'
    TOOL_CONTENT: ClassVar[str] = 'Tool message content belongs to its result'
    MULTIMODAL_REPLACEMENT: ClassVar[str] = 'Text replacement cannot discard multimodal content'
    INVALID_STORED_CALLS: ClassVar[str] = 'Stored Tool calls require an array'
    EMPTY_TEXT: ClassVar[str] = ""

    VERSION: ClassVar[int] = 1

    role: ModelRole
    content: str | tuple[ContentPart, ...] | None = None
    calls: tuple[ToolCall, ...] = ()
    result: ToolResult | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, type(self).role.__name__, ModelRole(self.role))
        if self.content is not None and not isinstance(self.content, str) and not (
                isinstance(self.content, tuple) and all(isinstance(part, ContentPart) for part in self.content)):
            raise TypeError(self.INVALID_CONTENT)
        if not isinstance(self.calls, tuple) or any(not isinstance(call, ToolCall) for call in self.calls):
            raise TypeError(self.INVALID_CALLS)
        if len({call.id for call in self.calls}) != len(self.calls) or (self.calls and self.role != ModelRole.ASSISTANT):
            raise ValueError(self.DUPLICATE_CALLS)
        if self.result is not None and (not isinstance(self.result, ToolResult) or self.role != ModelRole.TOOL or self.calls):
            raise ValueError(self.INVALID_RESULT)
        if self.role == ModelRole.TOOL and (self.result is None or self.content is not None):
            raise ValueError(self.TOOL_CONTENT)
        object.__setattr__(self, type(self).content.__name__, copy_model(self.content))
        object.__setattr__(self, type(self).calls.__name__, copy_model(self.calls))
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))

    @property
    def text(self):
        if self.result is not None:
            return self.result.content
        if isinstance(self.content, tuple):
            return self.EMPTY_TEXT.join(part.value for part in self.content if part.kind == ContentKind.TEXT)
        return self.EMPTY_TEXT if self.content is None else self.content

    def with_text(self, text):
        if self.result is not None:
            return replace(self, result=replace(self.result, content=text))
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError(self.MULTIMODAL_REPLACEMENT)
        return replace(self, content=text)

    def to_dict(self):
        return {ModelRecord.VERSION_FIELD: self.VERSION,
                type(self).role.__name__: self.role.value,
                type(self).content.__name__: [part.to_dict() for part in self.content] if isinstance(self.content, tuple) else self.content,
                type(self).calls.__name__: [call.to_dict() for call in self.calls],
                type(self).result.__name__: self.result.to_dict() if self.result is not None else self.result,
                type(self).opaque.__name__: _object(self.opaque)}

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=ModelRecord.VERSIONED)
        if isinstance(value[cls.content.__name__], list):
            value[cls.content.__name__] = tuple(ContentPart.from_dict(part) for part in value[cls.content.__name__])
        if not isinstance(value[cls.calls.__name__], list):
            raise TypeError(cls.INVALID_STORED_CALLS)
        value[cls.calls.__name__] = tuple(ToolCall.from_dict(call) for call in value[cls.calls.__name__])
        if value[cls.result.__name__] is not None:
            value[cls.result.__name__] = ToolResult.from_dict(value[cls.result.__name__])
        return cls(**value)


@dataclass(slots=True)
class ModelRequest:
    INVALID_MESSAGES: ClassVar[str] = 'Request requires typed model messages'
    INVALID_PARAMETERS: ClassVar[str] = 'Model parameters require text keys'
    RESERVED_FIELDS: ClassVar[str] = 'Messages and Tools belong in typed request fields'
    INVALID_TOOLS: ClassVar[str] = 'Request requires typed Tool definitions'
    DUPLICATE_TOOLS: ClassVar[str] = 'Duplicate Tool definition'
    REPEATED_CALLS: ClassVar[str] = 'Overlapping or repeated Tool calls'
    UNMATCHED_RESULT: ClassVar[str] = 'Unmatched or duplicate Tool result'
    INCOMPLETE_EXCHANGE: ClassVar[str] = 'Incomplete Tool exchange'
    MISSING_RESULTS: ClassVar[str] = 'Missing Tool results'
    INVALID_STORED_COLLECTIONS: ClassVar[str] = 'Stored messages and Tools require arrays'
    COMPLETE_EXCHANGES: ClassVar[bool] = True

    VERSION: ClassVar[int] = 1
    RESERVED_PARAMETERS: ClassVar[frozenset[str]] = frozenset(("messages", "tools"))

    messages: list[ModelMessage] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    tools: tuple[ToolDefinition, ...] = ()
    tool_mode: ToolMode | None = None

    def __post_init__(self):
        if not isinstance(self.messages, list) or any(not isinstance(message, ModelMessage) for message in self.messages):
            raise TypeError(self.INVALID_MESSAGES)
        if not isinstance(self.parameters, dict) or any(not isinstance(key, str) for key in self.parameters):
            raise TypeError(self.INVALID_PARAMETERS)
        if self.RESERVED_PARAMETERS.intersection(self.parameters):
            raise ValueError(self.RESERVED_FIELDS)
        if not isinstance(self.tools, tuple) or any(not isinstance(tool, ToolDefinition) for tool in self.tools):
            raise TypeError(self.INVALID_TOOLS)
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError(self.DUPLICATE_TOOLS)
        if self.tool_mode is not None:
            self.tool_mode = ToolMode(self.tool_mode)
        self.messages = copy_model(self.messages)
        self.parameters = copy_model(self.parameters)
        self.tools = copy_model(self.tools)

    def validate_exchanges(self, *, complete=COMPLETE_EXCHANGES):
        pending, seen = set(), set()
        for message in self.messages:
            if message.calls:
                identifiers = {call.id for call in message.calls}
                if pending or seen.intersection(identifiers):
                    raise ValueError(self.REPEATED_CALLS)
                pending = identifiers
                seen.update(identifiers)
            elif message.result is not None:
                if message.result.call_id not in pending:
                    raise ValueError(self.UNMATCHED_RESULT)
                pending.remove(message.result.call_id)
            elif pending:
                raise ValueError(self.INCOMPLETE_EXCHANGE)
        if pending and complete:
            raise ValueError(self.MISSING_RESULTS)

    def to_dict(self):
        return {ModelRecord.VERSION_FIELD: self.VERSION,
                type(self).messages.__name__: [message.to_dict() for message in self.messages],
                type(self).parameters.__name__: _object(self.parameters),
                type(self).tools.__name__: [tool.to_dict() for tool in self.tools],
                type(self).tool_mode.__name__: self.tool_mode.value if self.tool_mode is not None else self.tool_mode}

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=ModelRecord.VERSIONED)
        if not isinstance(value[cls.messages.__name__], list) or not isinstance(value[cls.tools.__name__], list):
            raise TypeError(cls.INVALID_STORED_COLLECTIONS)
        value[cls.messages.__name__] = [ModelMessage.from_dict(message) for message in value[cls.messages.__name__]]
        value[cls.tools.__name__] = tuple(ToolDefinition.from_dict(tool) for tool in value[cls.tools.__name__])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    INVALID_MESSAGE: ClassVar[str] = 'Model response requires an assistant message'
    INVALID_IDENTITY: ClassVar[str] = 'Model response identities must be text or None'

    VERSION: ClassVar[int] = 1

    message: ModelMessage
    stop: StopReason = StopReason.UNKNOWN
    usage: dict | None = None
    response_id: str | None = None
    model: str | None = None
    raw_finish_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.message, ModelMessage) or self.message.role != ModelRole.ASSISTANT:
            raise ValueError(self.INVALID_MESSAGE)
        object.__setattr__(self, type(self).message.__name__, copy_model(self.message))
        object.__setattr__(self, type(self).stop.__name__, StopReason(self.stop))
        if any(value is not None and not isinstance(value, str)
               for value in (self.response_id, self.model, self.raw_finish_reason)):
            raise TypeError(self.INVALID_IDENTITY)
        object.__setattr__(self, type(self).usage.__name__, _usage(self.usage))

    def to_dict(self):
        return {ModelRecord.VERSION_FIELD: self.VERSION,
                type(self).message.__name__: self.message.to_dict(),
                type(self).stop.__name__: self.stop.value,
                type(self).usage.__name__: _usage(self.usage),
                type(self).response_id.__name__: self.response_id,
                type(self).model.__name__: self.model,
                type(self).raw_finish_reason.__name__: self.raw_finish_reason}

    @classmethod
    def from_dict(cls, value):
        value = _record(cls, value, versioned=ModelRecord.VERSIONED)
        value[cls.message.__name__] = ModelMessage.from_dict(value[cls.message.__name__])
        return cls(**value)


class ModelOperation(StrEnum):
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    RERANK = "rerank"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    usage: dict | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        from .embeddings import validate_vectors
        object.__setattr__(self, type(self).vector.__name__, tuple(validate_vectors([self.vector])[0]))
        object.__setattr__(self, type(self).usage.__name__, _usage(self.usage))
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))


@dataclass(frozen=True, slots=True)
class RankedDocument:
    INVALID: ClassVar[str] = 'Ranking requires a nonnegative index and finite score'
    MINIMUM_INDEX: ClassVar[int] = 0

    index: int
    score: float

    def __post_init__(self):
        if (type(self.index) is not int or self.index < self.MINIMUM_INDEX or type(self.score) not in (int, float)
                or not math.isfinite(self.score)):
            raise ValueError(self.INVALID)


@dataclass(frozen=True, slots=True)
class RerankResult:
    INVALID_ROWS: ClassVar[str] = 'Ranking requires typed document rows'
    DUPLICATE_INDEX: ClassVar[str] = 'Duplicate ranking index'

    rows: tuple[RankedDocument, ...]
    usage: dict | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.rows, tuple) or any(not isinstance(row, RankedDocument) for row in self.rows):
            raise TypeError(self.INVALID_ROWS)
        if len({row.index for row in self.rows}) != len(self.rows):
            raise ValueError(self.DUPLICATE_INDEX)
        object.__setattr__(self, type(self).usage.__name__, _usage(self.usage))
        object.__setattr__(self, type(self).opaque.__name__, _object(self.opaque))


@dataclass(frozen=True, slots=True)
class CompletionUpdate:
    """Normalized streaming observations; raw termination is evidence only."""

    INVALID_TEXT: ClassVar[str] = 'Completion deltas must be text'
    INVALID_IDENTITY: ClassVar[str] = 'Completion identities and raw termination must be text or None'

    text: str = ""
    reasoning: str = ""
    response_id: str | None = None
    model: str | None = None
    usage: dict | None = None
    finish_reason: str | None = None
    termination: StopReason | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or not isinstance(self.reasoning, str):
            raise TypeError(self.INVALID_TEXT)
        if any(value is not None and not isinstance(value, str)
               for value in (self.response_id, self.model, self.finish_reason)):
            raise TypeError(self.INVALID_IDENTITY)
        object.__setattr__(self, type(self).usage.__name__, _usage(self.usage))
        if self.termination is not None:
            object.__setattr__(self, type(self).termination.__name__, StopReason(self.termination))


def copy_model(value):
    """Detach model/options containers while preserving runtime client identity."""
    if isinstance(value, ModelRequest):
        return ModelRequest(value.messages, value.parameters, value.tools, value.tool_mode)
    if isinstance(value, (CompletionUpdate, EmbeddingResult, RankedDocument, RerankResult)):
        return replace(value)
    if isinstance(value, (ModelMessage, ModelResponse, ContentPart, ToolCall, ToolResult, ToolDefinition)):
        return type(value).from_dict(value.to_dict())
    if isinstance(value, dict):
        return {key: copy_model(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_model(item) for item in value]
    if isinstance(value, tuple):
        return tuple(copy_model(item) for item in value)
    return value
