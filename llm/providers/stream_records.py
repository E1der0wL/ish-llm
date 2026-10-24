"""LiteLLM streamed wire Readers and the mutable Tool-fragment Writer.

Incomplete wire fragments never become executable calls; the completed message
still passes the existing codec and common model validation.
"""

from collections.abc import Mapping

from llm.core.model_protocol import CompletionUpdate, ModelRole
from .response_values import SDKValue, SDKMessage, SDKOperationResponse, SDKCompletionResponse
from .request_values import LiteLLMParameters
from .wire_codec import SDKToolCodec
from .streaming import CompletionError, CompletionErrorCode


class SDKStreamMetadata:
    FIELDS = (
        (CompletionUpdate.response_id.__name__, SDKOperationResponse.ID),
        (CompletionUpdate.model.__name__, SDKOperationResponse.MODEL),
    )
    UNSPECIFIED = None

    def __init__(self, chunk):
        self.attributes = {
            key: value
            for key, source in self.FIELDS
            if isinstance(value := SDKValue.field(chunk, source), str)
        }
        self.usage = SDKValue.usage(SDKValue.field(chunk, SDKOperationResponse.USAGE))

    def response_id(self, previous):
        return self.attributes.get(CompletionUpdate.response_id.__name__, previous)

    def model(self, previous):
        return self.attributes.get(CompletionUpdate.model.__name__, previous)

    @property
    def present(self):
        return self.attributes or self.usage

    def update(self):
        return CompletionUpdate(**self.attributes, usage=self.usage or self.UNSPECIFIED)


class SDKStreamChoice:
    INDEX = "index"
    DELTA = "delta"
    FIRST_INDEX = 0
    INVALID_CHOICES = "Expected a completion choices array"
    INVALID_CHOICE = SDKCompletionResponse.INVALID_CHOICES
    UNSPECIFIED = None

    def __init__(self, choice):
        self._value = choice

    @classmethod
    def read(cls, chunk):
        choices = SDKValue.field(chunk, SDKCompletionResponse.CHOICES, [])
        if not isinstance(choices, (list, tuple)):
            raise CompletionError(cls.INVALID_CHOICES)
        if not choices:
            return cls.UNSPECIFIED
        if (
            len(choices) != SDKCompletionResponse.SINGLE_CHOICE
            or SDKValue.field(choices[cls.FIRST_INDEX], cls.INDEX, cls.FIRST_INDEX) != cls.FIRST_INDEX
        ):
            raise CompletionError(cls.INVALID_CHOICE)
        return cls(choices[cls.FIRST_INDEX])

    @property
    def delta(self):
        return SDKStreamDelta(SDKValue.field(self._value, self.DELTA))

    @property
    def finish_reason(self):
        return SDKValue.field(self._value, SDKCompletionResponse.FINISH_REASON)


class SDKStreamDelta:
    REASONING = "reasoning_content"
    RESERVED = (SDKMessage.ROLE, SDKMessage.CONTENT, SDKMessage.TOOL_CALLS, LiteLLMParameters.FUNCTION_CALL)
    UNSPECIFIED = None

    def __init__(self, delta):
        self._value = delta

    def merge_opaque(self, target):
        raw_delta = SDKValue.mapping(self._value)
        if isinstance(raw_delta, Mapping):
            for key, value in raw_delta.items():
                if key not in self.RESERVED and value is not self.UNSPECIFIED:
                    if key == self.REASONING and isinstance(value, str) and isinstance(target.get(key), str):
                        target[key] += value
                    else:
                        target[key] = SDKValue.merge_opaque(target.get(key), value)

    @property
    def content(self):
        return SDKValue.field(self._value, SDKMessage.CONTENT)

    @property
    def reasoning(self):
        return SDKValue.field(self._value, self.REASONING)

    @property
    def tool_calls(self):
        return SDKValue.field(self._value, SDKMessage.TOOL_CALLS)

    @property
    def legacy_function_call(self):
        return SDKValue.field(self._value, LiteLLMParameters.FUNCTION_CALL)


class SDKToolFragment:
    """One partial SDK call; owns its wire mutation and extension preservation."""

    INDEX = SDKStreamChoice.INDEX
    OUTER_FIELDS = {INDEX, *SDKToolCodec.CALL_FIELDS}
    FUNCTION_FIELDS = SDKToolCodec.FUNCTION_FIELDS
    EMPTY_TEXT = ""
    NAME_LIMIT = 64
    UNSPECIFIED = None
    INVALID_TYPE = "Unsupported tool call type"
    INVALID_FRAGMENT = "Invalid tool call fragment"
    SIZE_LIMIT = "Tool call size limit exceeded"
    NAME_TOO_LONG = "Tool call name exceeds protocol limit"

    def __init__(self):
        self._value = {
            SDKToolCodec.ID: self.EMPTY_TEXT,
            SDKToolCodec.TYPE: SDKToolCodec.FUNCTION_KIND,
            SDKToolCodec.FUNCTION: {
                SDKToolCodec.NAME: self.EMPTY_TEXT,
                SDKToolCodec.ARGUMENTS: self.EMPTY_TEXT,
            },
        }

    @classmethod
    def index(cls, fragment):
        return SDKValue.field(fragment, cls.INDEX)

    @classmethod
    def validate_type(cls, fragment):
        if SDKValue.field(fragment, SDKToolCodec.TYPE) not in (cls.UNSPECIFIED, SDKToolCodec.FUNCTION_KIND):
            raise CompletionError(cls.INVALID_TYPE)

    def append(self, fragment):
        call = self._value
        function = SDKValue.field(fragment, SDKToolCodec.FUNCTION)
        for target, raw, reserved in (
            (call, SDKValue.mapping(fragment), self.OUTER_FIELDS),
            (call[SDKToolCodec.FUNCTION], SDKValue.mapping(function), self.FUNCTION_FIELDS),
        ):
            if isinstance(raw, Mapping):
                for key, value in raw.items():
                    if key not in reserved and value is not self.UNSPECIFIED:
                        target[key] = SDKValue.merge_opaque(target.get(key), value)
        for target, key, value in (
            (call, SDKToolCodec.ID, SDKValue.field(fragment, SDKToolCodec.ID)),
            (call[SDKToolCodec.FUNCTION], SDKToolCodec.NAME, SDKValue.field(function, SDKToolCodec.NAME)),
            (
                call[SDKToolCodec.FUNCTION],
                SDKToolCodec.ARGUMENTS,
                SDKValue.field(function, SDKToolCodec.ARGUMENTS),
            ),
        ):
            if value is not self.UNSPECIFIED:
                if not isinstance(value, str):
                    raise CompletionError(self.INVALID_FRAGMENT)
                target[key] += value

    def validate_size(self, maximum):
        if maximum is not self.UNSPECIFIED and (
            len(self._value[SDKToolCodec.FUNCTION][SDKToolCodec.ARGUMENTS]) > maximum
            or len(self._value[SDKToolCodec.ID]) > maximum
        ):
            raise CompletionError(self.SIZE_LIMIT, code=CompletionErrorCode.ARGUMENT_LIMIT)
        if len(self._value[SDKToolCodec.FUNCTION][SDKToolCodec.NAME]) > self.NAME_LIMIT:
            raise CompletionError(self.NAME_TOO_LONG)

    @property
    def id(self):
        return self._value[SDKToolCodec.ID]

    @property
    def incomplete(self):
        return not self._value[SDKToolCodec.ID] or not self._value[SDKToolCodec.FUNCTION][SDKToolCodec.NAME]

    def to_wire(self):
        return self._value


class SDKStreamMessage:
    EMPTY_TEXT = ""
    UNSPECIFIED = None

    @classmethod
    def create(cls, opaque, content_parts, calls):
        result = {
            **opaque,
            SDKMessage.ROLE: ModelRole.ASSISTANT.value,
            SDKMessage.CONTENT: cls.EMPTY_TEXT.join(content_parts) or cls.UNSPECIFIED,
        }
        if calls:
            result[SDKMessage.TOOL_CALLS] = [calls[index].to_wire() for index in sorted(calls)]
        return result
