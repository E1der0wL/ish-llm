"""Provider-independent invocation values, separate from persisted conversation records.

Only adapters interpret opaque state. JSON encodings are versioned and contain no
SDK objects. Runtime options may retain client/callback identity until transport.
"""

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
import math


def json_copy(value):
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            for child in item.values():
                check(child)
            return
        raise ValueError('Model state must contain finite JSON values')
    check(value)
    return deepcopy(value)


def opaque_copy(value):
    if not isinstance(value, dict):
        raise ValueError('Opaque adapter state requires a JSON object')
    return json_copy(value)


def option_copy(value):
    """Copy containers and model values, preserving opaque runtime handle identity."""
    if isinstance(value, dict):
        return {k: option_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [option_copy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(option_copy(v) for v in value)
    if isinstance(value, ModelRequest):
        return replace(value, parameters=option_copy(value.parameters), messages=deepcopy(value.messages),
                       tools=deepcopy(value.tools))
    if isinstance(value, (ModelMessage, ModelResponse, ToolDefinition, ToolCall, ToolResult, ContentPart)):
        return deepcopy(value)
    return value


class StopReason(StrEnum):
    COMPLETE = 'complete'
    TOOLS = 'tools'
    OUTPUT_LIMIT = 'output_limit'
    BLOCKED = 'blocked'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class ContentPart:
    kind: str
    value: str
    media_type: str | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ('text', 'image', 'audio', 'file', 'opaque') or not isinstance(self.value, str):
            raise ValueError('Invalid model content part')
        if self.media_type is not None and not isinstance(self.media_type, str):
            raise TypeError('Media type must be text')
        object.__setattr__(self, 'opaque', opaque_copy(self.opaque))

    def to_dict(self):
        return dict(kind=self.kind, value=self.value, media_type=self.media_type, opaque=json_copy(self.opaque))


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if any(not isinstance(v, str) or not v for v in (self.id, self.name)) or not isinstance(self.arguments, dict):
            raise ValueError('A completed Tool call requires ID, name and argument object')
        object.__setattr__(self, 'arguments', json_copy(self.arguments))
        object.__setattr__(self, 'opaque', opaque_copy(self.opaque))

    def to_dict(self):
        return dict(id=self.id, name=self.name, arguments=json_copy(self.arguments), opaque=json_copy(self.opaque))


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    content: str
    name: str | None = None

    def __post_init__(self):
        if not isinstance(self.call_id, str) or not self.call_id or not isinstance(self.content, str):
            raise ValueError('Tool result requires call ID and text')
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError('Tool result name must be nonempty')

    def to_dict(self):
        return dict(call_id=self.call_id, content=self.content, name=self.name)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    strict: bool | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name or not isinstance(self.description, str):
            raise ValueError('Invalid Tool definition identity')
        if not isinstance(self.parameters, dict) or self.parameters.get('type') != 'object':
            raise ValueError('Tool parameters must describe an object')
        if self.strict is not None and type(self.strict) is not bool:
            raise TypeError('strict must be boolean or None')
        object.__setattr__(self, 'parameters', json_copy(self.parameters))
        object.__setattr__(self, 'opaque', opaque_copy(self.opaque))

    def to_dict(self):
        return dict(name=self.name, description=self.description, parameters=json_copy(self.parameters),
                    strict=self.strict, opaque=json_copy(self.opaque))


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str | tuple[ContentPart, ...] | None = None
    calls: tuple[ToolCall, ...] = ()
    result: ToolResult | None = None
    opaque: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.role not in ('system', 'developer', 'user', 'assistant', 'tool'):
            raise ValueError('Invalid model message role')
        if self.content is not None and not isinstance(self.content, str) and not (
                isinstance(self.content, tuple) and all(isinstance(p, ContentPart) for p in self.content)):
            raise TypeError('Model content requires text or typed content parts')
        if not isinstance(self.calls, tuple) or any(not isinstance(c, ToolCall) for c in self.calls):
            raise TypeError('Model calls require completed ToolCall values')
        if len({c.id for c in self.calls}) != len(self.calls) or self.calls and self.role != 'assistant':
            raise ValueError('Invalid or duplicate model Tool calls')
        if self.result is not None and (not isinstance(self.result, ToolResult) or self.role != 'tool' or self.calls):
            raise ValueError('Invalid Tool result message')
        if self.role == 'tool' and (self.result is None or self.content is not None):
            raise ValueError('Tool content belongs to the ToolResult')
        object.__setattr__(self, 'opaque', opaque_copy(self.opaque))

    @property
    def text(self):
        if self.result is not None:
            return self.result.content
        if isinstance(self.content, tuple):
            return ''.join(p.value for p in self.content if p.kind == 'text')
        return self.content or ''

    def with_text(self, text):
        if self.result is not None:
            return replace(self, result=replace(self.result, content=text))
        if self.content is not None and not isinstance(self.content, str):
            raise ValueError('Cannot replace multimodal content with text')
        return replace(self, content=text)

    def to_dict(self):
        return dict(version=1, role=self.role,
            content=[p.to_dict() for p in self.content] if isinstance(self.content, tuple) else self.content,
            calls=[c.to_dict() for c in self.calls], result=self.result.to_dict() if self.result else None,
            opaque=json_copy(self.opaque))

    @classmethod
    def from_dict(cls, value):
        value = json_copy(value)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or set(value) != {'version', 'role', 'content', 'calls', 'result', 'opaque'}):
            raise ValueError('Unsupported model message version')
        value.pop('version')
        if isinstance(value.get('content'), list):
            value['content'] = tuple(ContentPart(**p) for p in value['content'])
        value['calls'] = tuple(ToolCall(**c) for c in value.get('calls', ()))
        if value.get('result') is not None:
            value['result'] = ToolResult(**value['result'])
        return cls(**value)


@dataclass(slots=True)
class ModelRequest:
    messages: list[ModelMessage] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    tools: tuple[ToolDefinition, ...] = ()
    tool_mode: str | None = None

    def __post_init__(self):
        if not isinstance(self.messages, list) or any(not isinstance(m, ModelMessage) for m in self.messages):
            raise TypeError('Model request requires typed messages')
        if not isinstance(self.parameters, dict) or any(not isinstance(k, str) for k in self.parameters):
            raise TypeError('Model options require a string-keyed mapping')
        if set(self.parameters) & {'messages', 'tools'}:
            raise ValueError('Messages and Tools must use typed request fields')
        if not isinstance(self.tools, tuple) or any(not isinstance(t, ToolDefinition) for t in self.tools):
            raise TypeError('Model request requires typed Tool definitions')
        if len({t.name for t in self.tools}) != len(self.tools):
            raise ValueError('Duplicate model Tool definition')
        if self.tool_mode not in (None, 'required', 'auto', 'none'):
            raise ValueError('Invalid model Tool selection')

    def validate_exchanges(self, *, complete=True):
        pending, seen = set(), set()
        for message in self.messages:
            if message.calls:
                if pending or seen.intersection(c.id for c in message.calls):
                    raise ValueError('Overlapping or repeated Tool calls')
                pending = {c.id for c in message.calls}
                seen.update(pending)
            elif message.result:
                if message.result.call_id not in pending:
                    raise ValueError('Unmatched or duplicate Tool result')
                pending.remove(message.result.call_id)
            elif pending:
                raise ValueError('Incomplete Tool exchange')
        if pending and complete:
            raise ValueError('Missing Tool results')

    def to_dict(self):
        return dict(version=1, messages=[m.to_dict() for m in self.messages],
                    parameters=json_copy(self.parameters), tools=[t.to_dict() for t in self.tools], tool_mode=self.tool_mode)

    @classmethod
    def from_dict(cls, value):
        value=json_copy(value)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or set(value) != {'version', 'messages', 'parameters', 'tools', 'tool_mode'}):
            raise ValueError('Unsupported model request version')
        value.pop('version')
        return cls([ModelMessage.from_dict(m) for m in value['messages']], value['parameters'],
                   tuple(ToolDefinition(**t) for t in value['tools']),value['tool_mode'])


@dataclass(frozen=True, slots=True)
class ModelResponse:
    message: ModelMessage
    stop: StopReason = StopReason.UNKNOWN
    usage: dict | None = None
    response_id: str | None = None
    model: str | None = None
    raw_finish_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.message, ModelMessage) or self.message.role != 'assistant':
            raise ValueError('Model response requires an assistant message')
        object.__setattr__(self, 'stop', StopReason(self.stop))
        if any(v is not None and not isinstance(v, str)
               for v in (self.response_id, self.model, self.raw_finish_reason)):
            raise TypeError('Response identities must be text or None')
        if self.usage is not None:
            def validate(value):
                if not isinstance(value, dict):
                    raise ValueError('Usage requires normalized counts')
                for k, v in value.items():
                    if not isinstance(k, str):
                        raise ValueError('Usage keys must be text')
                    if isinstance(v, dict):
                        validate(v)
                    elif type(v) is not int or v < 0:
                        raise ValueError('Usage must contain nonnegative integers')
            validate(self.usage)
            object.__setattr__(self, 'usage', json_copy(self.usage))

    def to_dict(self):
        return dict(version=1, message=self.message.to_dict(), stop=str(self.stop), usage=json_copy(self.usage),
                    response_id=self.response_id, model=self.model, raw_finish_reason=self.raw_finish_reason)

    @classmethod
    def from_dict(cls, value):
        value=json_copy(value)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or set(value) != {'version', 'message', 'stop', 'usage', 'response_id', 'model', 'raw_finish_reason'}):
            raise ValueError('Unsupported model response version')
        value.pop('version')
        value['message']=ModelMessage.from_dict(value['message'])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CompletionUpdate:
    text: str = ""
    reasoning: str = ""
    response_id: str | None = None
    model: str | None = None
    usage: dict | None = None
    finish_reason: str | None = None  # Raw provider metadata; control flow uses termination.
    termination: StopReason | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or not isinstance(self.reasoning, str):
            raise TypeError("Completion deltas must be text")
        if any(value is not None and not isinstance(value, str)
               for value in (self.response_id, self.model, self.finish_reason)):
            raise TypeError("Completion identities and termination must be text or None")
        if self.usage is not None:
            def validate(value):
                if not isinstance(value, dict):
                    raise TypeError("Completion usage must be a normalized dictionary")
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("Completion usage keys must be text")
                    if isinstance(item, dict):
                        validate(item)
                    elif type(item) is not int or item < 0:
                        raise ValueError("Completion usage must contain nonnegative integers")
            validate(self.usage)
            object.__setattr__(self, "usage", json_copy(self.usage))
        if self.termination is not None:
            object.__setattr__(self, "termination", StopReason(self.termination))



@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    usage: dict | None = None

    def __post_init__(self):
        if (not isinstance(self.vector, tuple) or not self.vector or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in self.vector)):
            raise ValueError('Embedding requires a finite nonempty vector')
        checked = CompletionUpdate(usage=self.usage)
        object.__setattr__(self, 'usage', checked.usage)


@dataclass(frozen=True, slots=True)
class RankedDocument:
    index: int
    score: float

    def __post_init__(self):
        if (type(self.index) is not int or self.index < 0 or
                type(self.score) not in (int, float) or not math.isfinite(self.score)):
            raise ValueError('Ranking requires a nonnegative index and finite score')


@dataclass(frozen=True, slots=True)
class RerankResult:
    documents: tuple[RankedDocument, ...]
    usage: dict | None = None

    def __post_init__(self):
        if (not isinstance(self.documents, tuple) or
                any(not isinstance(row, RankedDocument) for row in self.documents) or
                len({row.index for row in self.documents}) != len(self.documents)):
            raise ValueError('Ranking requires unique typed document results')
        checked = CompletionUpdate(usage=self.usage)
        object.__setattr__(self, 'usage', checked.usage)
