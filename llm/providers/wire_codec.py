"""LiteLLM wire conversion. Persistent common records use their own strict readers."""

from collections.abc import Mapping
import json

from llm.core.model_protocol import (
    ContentKind,
    ContentPart,
    ModelMessage,
    ModelRequest,
    ModelRole,
    StopReason,
    ToolCall,
    ToolDefinition,
    ToolResult,
    copy_model,
    json_copy,
)


from .request_values import LiteLLMParameters
from .response_values import SDKMessage


class SDKWire:
    STATE = "litellm"
    SHAPE = "litellm_wire"
    UNSPECIFIED = None
    ENSURE_ASCII = False
    ALLOW_NON_FINITE = False
    SORT_KEYS = True
    CANONICAL_SEPARATORS = (",", ":")
    INVALID_OBJECT = "SDK value must be an object"
    RESERVED_EXTENSION = "Opaque extensions cannot replace common model fields"
    INVALID_ARGUMENTS = "SDK Tool arguments require completed JSON text"
    NON_FINITE_ARGUMENTS = "Tool arguments must be finite JSON"

    @classmethod
    def object(cls, value):
        if not isinstance(value, Mapping):
            raise TypeError(SDKWire.INVALID_OBJECT)
        return json_copy(dict(value))

    @classmethod
    def state(cls, value, key=STATE):
        return SDKWire.object(value[key]) if key in value else {}

    @classmethod
    def extras(cls, value, reserved):
        return {key: item for key, item in value.items() if key not in reserved}

    @classmethod
    def extension(cls, value, reserved):
        value = SDKWire.object(value)
        if reserved.intersection(value):
            raise ValueError(SDKWire.RESERVED_EXTENSION)
        return value

    @classmethod
    def arguments(cls, text):
        if not isinstance(text, str):
            raise TypeError(SDKWire.INVALID_ARGUMENTS)

        def reject_constant(value):
            raise ValueError(SDKWire.NON_FINITE_ARGUMENTS)

        return SDKWire.object(json.loads(text, parse_constant=reject_constant))

    @classmethod
    def canonical(cls, value):
        return json.dumps(
            value,
            sort_keys=SDKWire.SORT_KEYS,
            ensure_ascii=SDKWire.ENSURE_ASCII,
            allow_nan=SDKWire.ALLOW_NON_FINITE,
            separators=SDKWire.CANONICAL_SEPARATORS,
        )


class SDKFinishReason:
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    NORMALIZED = {
        STOP: StopReason.COMPLETE,
        TOOL_CALLS: StopReason.TOOLS,
        LENGTH: StopReason.OUTPUT_LIMIT,
        CONTENT_FILTER: StopReason.BLOCKED,
    }
    UNSPECIFIED = None
    INVALID_REASON = "SDK finish reason must be text or None"

    @classmethod
    def normalize(cls, reason):
        if reason is not SDKFinishReason.UNSPECIFIED and not isinstance(reason, str):
            raise TypeError(SDKFinishReason.INVALID_REASON)
        return SDKFinishReason.NORMALIZED.get(reason, StopReason.UNKNOWN)


class SDKToolCodec:
    ID = "id"
    TYPE = "type"
    FUNCTION = "function"
    FUNCTION_KIND = FUNCTION
    NAME = "name"
    ARGUMENTS = "arguments"
    DESCRIPTION = "description"
    PARAMETERS = "parameters"
    STRICT = "strict"
    ARGUMENTS_TEXT = "arguments_text"
    TYPE_MISSING = "type_missing"
    EXTRA = "extra"
    FUNCTION_EXTRA = "function_extra"
    DESCRIPTION_MISSING = "description_missing"
    STRICT_PRESENT = "strict_present"
    OUTER = "outer"
    CALL_FIELDS = frozenset((ID, TYPE, FUNCTION))
    FUNCTION_FIELDS = frozenset((NAME, ARGUMENTS))
    DEFINITION_FIELDS = frozenset((NAME, DESCRIPTION, PARAMETERS, STRICT))
    CALL_STATE_FIELDS = frozenset((ARGUMENTS_TEXT, TYPE_MISSING, EXTRA, FUNCTION_EXTRA))
    DEFINITION_STATE_FIELDS = frozenset((DESCRIPTION_MISSING, STRICT_PRESENT, OUTER, FUNCTION))
    UNSPECIFIED = None
    PRESENCE_DEFAULT = False
    EMPTY_DESCRIPTION = ""
    INVALID_CALL_STATE = "SDK Tool state requires all current fields"
    INVALID_ORIGINAL_ARGUMENTS = "Original SDK Tool arguments require text"
    INVALID_TYPE_PRESENCE = "SDK type presence must be boolean"
    INVALID_DEFINITION_TYPE = "Unsupported SDK Tool definition"
    INCOMPLETE_DEFINITION = "Incomplete SDK Tool definition"
    INVALID_DEFINITION = "SDK encoding requires ToolDefinition"
    INVALID_DEFINITION_STATE = "SDK definition state requires all current fields"
    INVALID_DEFINITION_PRESENCE = "SDK definition presence must be boolean"

    INVALID_CALL_TYPE = "Unsupported SDK Tool call type"
    INCOMPLETE_CALL = "Incomplete SDK Tool call"

    @classmethod
    def observed_identifier(cls, call):
        """Diagnostic correlation only; execution uses the complete call Reader."""
        return call.get(cls.ID) if isinstance(call, Mapping) else cls.UNSPECIFIED

    @classmethod
    def decode_call(cls, call):
        call = SDKWire.object(call)
        if SDKToolCodec.TYPE in call and call[SDKToolCodec.TYPE] != SDKToolCodec.FUNCTION_KIND:
            raise ValueError(SDKToolCodec.INVALID_CALL_TYPE)
        function = SDKWire.object(call.get(SDKToolCodec.FUNCTION))
        if (
            not {SDKToolCodec.ID} <= call.keys()
            or not {SDKToolCodec.NAME, SDKToolCodec.ARGUMENTS} <= function.keys()
        ):
            raise ValueError(SDKToolCodec.INCOMPLETE_CALL)
        arguments = SDKWire.arguments(function[SDKToolCodec.ARGUMENTS])
        state = {
            SDKToolCodec.ARGUMENTS_TEXT: function[SDKToolCodec.ARGUMENTS],
            SDKToolCodec.TYPE_MISSING: SDKToolCodec.TYPE not in call,
            SDKToolCodec.EXTRA: SDKWire.extras(call, SDKToolCodec.CALL_FIELDS),
            SDKToolCodec.FUNCTION_EXTRA: SDKWire.extras(function, SDKToolCodec.FUNCTION_FIELDS),
        }
        return ToolCall(call[SDKToolCodec.ID], function[SDKToolCodec.NAME], arguments, {SDKWire.STATE: state})

    @classmethod
    def encode_call(cls, call):
        state = SDKWire.state(call.opaque)
        if SDKWire.STATE in call.opaque and state.keys() != SDKToolCodec.CALL_STATE_FIELDS:
            raise ValueError(SDKToolCodec.INVALID_CALL_STATE)
        text = state.get(SDKToolCodec.ARGUMENTS_TEXT)
        if SDKWire.STATE in call.opaque and not isinstance(text, str):
            raise TypeError(SDKToolCodec.INVALID_ORIGINAL_ARGUMENTS)
        # Python equality conflates False/0 and 1/1.0. Preserve original wire text only
        # when its JSON types and values still match the common argument object.
        if text is SDKToolCodec.UNSPECIFIED or SDKWire.canonical(
            SDKWire.arguments(text)
        ) != SDKWire.canonical(call.arguments):
            text = json.dumps(
                call.arguments, ensure_ascii=SDKWire.ENSURE_ASCII, allow_nan=SDKWire.ALLOW_NON_FINITE
            )
        missing = state.get(SDKToolCodec.TYPE_MISSING, SDKToolCodec.PRESENCE_DEFAULT)
        if type(missing) is not bool:
            raise TypeError(SDKToolCodec.INVALID_TYPE_PRESENCE)
        return {
            **SDKWire.extension(state.get(SDKToolCodec.EXTRA, {}), SDKToolCodec.CALL_FIELDS),
            SDKToolCodec.ID: call.id,
            **({} if missing else {SDKToolCodec.TYPE: SDKToolCodec.FUNCTION_KIND}),
            SDKToolCodec.FUNCTION: {
                **SDKWire.extension(state.get(SDKToolCodec.FUNCTION_EXTRA, {}), SDKToolCodec.FUNCTION_FIELDS),
                SDKToolCodec.NAME: call.name,
                SDKToolCodec.ARGUMENTS: text,
            },
        }

    @classmethod
    def decode_definition(cls, value):
        value = SDKWire.object(value)
        if value.get(SDKToolCodec.TYPE) != SDKToolCodec.FUNCTION_KIND:
            raise ValueError(SDKToolCodec.INVALID_DEFINITION_TYPE)
        function = SDKWire.object(value.get(SDKToolCodec.FUNCTION))
        if not {SDKToolCodec.NAME, SDKToolCodec.PARAMETERS} <= function.keys():
            raise ValueError(SDKToolCodec.INCOMPLETE_DEFINITION)
        return ToolDefinition(
            function[SDKToolCodec.NAME],
            function.get(SDKToolCodec.DESCRIPTION, SDKToolCodec.EMPTY_DESCRIPTION),
            function[SDKToolCodec.PARAMETERS],
            function.get(SDKToolCodec.STRICT),
            {
                SDKWire.STATE: {
                    SDKToolCodec.DESCRIPTION_MISSING: SDKToolCodec.DESCRIPTION not in function,
                    SDKToolCodec.STRICT_PRESENT: SDKToolCodec.STRICT in function,
                    SDKToolCodec.OUTER: SDKWire.extras(value, {SDKToolCodec.TYPE, SDKToolCodec.FUNCTION}),
                    SDKToolCodec.FUNCTION: SDKWire.extras(function, SDKToolCodec.DEFINITION_FIELDS),
                }
            },
        )

    @classmethod
    def encode_definition(cls, definition):
        if not isinstance(definition, ToolDefinition):
            raise TypeError(SDKToolCodec.INVALID_DEFINITION)
        definition = copy_model(definition)
        state = SDKWire.state(definition.opaque)
        if SDKWire.STATE in definition.opaque and state.keys() != SDKToolCodec.DEFINITION_STATE_FIELDS:
            raise ValueError(SDKToolCodec.INVALID_DEFINITION_STATE)
        missing, present = state.get(
            SDKToolCodec.DESCRIPTION_MISSING, SDKToolCodec.PRESENCE_DEFAULT
        ), state.get(SDKToolCodec.STRICT_PRESENT, SDKToolCodec.PRESENCE_DEFAULT)
        if type(missing) is not bool or type(present) is not bool:
            raise TypeError(SDKToolCodec.INVALID_DEFINITION_PRESENCE)
        function = {
            **SDKWire.extension(state.get(SDKToolCodec.FUNCTION, {}), SDKToolCodec.DEFINITION_FIELDS),
            SDKToolCodec.NAME: definition.name,
            SDKToolCodec.PARAMETERS: definition.parameters,
        }
        if not missing or definition.description:
            function[SDKToolCodec.DESCRIPTION] = definition.description
        if present or definition.strict is not SDKToolCodec.UNSPECIFIED:
            function[SDKToolCodec.STRICT] = definition.strict
        return {
            **SDKWire.extension(
                state.get(SDKToolCodec.OUTER, {}), {SDKToolCodec.TYPE, SDKToolCodec.FUNCTION}
            ),
            SDKToolCodec.TYPE: SDKToolCodec.FUNCTION_KIND,
            SDKToolCodec.FUNCTION: function,
        }


class SDKContentPartCodec:
    TYPE = "type"
    TEXT = "text"
    IMAGE_URL = "image_url"
    URL = "url"
    INVALID_TEXT = "Opaque content kind changed"
    INVALID_IMAGE = "Opaque image shape changed"
    UNSUPPORTED_OPAQUE = "Unsupported opaque SDK content"
    MISSING_TYPE = "Opaque SDK content requires its type"
    MISSING_STATE = "This LiteLLM content kind requires its original adapter state"

    MISSING_PART_TYPE = "SDK content part requires its type"
    OPAQUE_PART_VALUE = ""

    @classmethod
    def decode_many(cls, content):
        parts = []
        for part in content:
            part = SDKWire.object(part)
            if not isinstance(part.get(SDKContentPartCodec.TYPE), str) or not part[SDKContentPartCodec.TYPE]:
                raise ValueError(SDKContentPartCodec.MISSING_PART_TYPE)
            if part.get(SDKContentPartCodec.TYPE) == SDKContentPartCodec.TEXT:
                parts.append(
                    ContentPart(
                        ContentKind.TEXT, part.get(SDKContentPartCodec.TEXT), opaque={SDKWire.STATE: part}
                    )
                )
            elif part.get(SDKContentPartCodec.TYPE) == SDKContentPartCodec.IMAGE_URL:
                image = SDKWire.object(part.get(SDKContentPartCodec.IMAGE_URL))
                parts.append(
                    ContentPart(
                        ContentKind.IMAGE, image.get(SDKContentPartCodec.URL), opaque={SDKWire.STATE: part}
                    )
                )
            else:
                parts.append(
                    ContentPart(
                        ContentKind.OPAQUE,
                        SDKContentPartCodec.OPAQUE_PART_VALUE,
                        opaque={SDKWire.STATE: part},
                    )
                )
        return tuple(parts)

    @classmethod
    def encode(cls, part):
        if SDKWire.STATE in part.opaque:
            value = SDKWire.state(part.opaque)
            if part.kind == ContentKind.TEXT:
                if value.get(SDKContentPartCodec.TYPE) != SDKContentPartCodec.TEXT or not isinstance(
                    value.get(SDKContentPartCodec.TEXT), str
                ):
                    raise ValueError(SDKContentPartCodec.INVALID_TEXT)
                value[SDKContentPartCodec.TEXT] = part.value
            elif part.kind == ContentKind.IMAGE:
                if (
                    value.get(SDKContentPartCodec.TYPE) != SDKContentPartCodec.IMAGE_URL
                    or not isinstance(value.get(SDKContentPartCodec.IMAGE_URL), dict)
                    or not isinstance(value[SDKContentPartCodec.IMAGE_URL].get(SDKContentPartCodec.URL), str)
                ):
                    raise ValueError(SDKContentPartCodec.INVALID_IMAGE)
                value[SDKContentPartCodec.IMAGE_URL][SDKContentPartCodec.URL] = part.value
            elif part.kind != ContentKind.OPAQUE:
                raise ValueError(SDKContentPartCodec.UNSUPPORTED_OPAQUE)
            elif (
                not isinstance(value.get(SDKContentPartCodec.TYPE), str)
                or not value[SDKContentPartCodec.TYPE]
            ):
                raise ValueError(SDKContentPartCodec.MISSING_TYPE)
            return value
        if part.kind == ContentKind.TEXT:
            return {SDKContentPartCodec.TYPE: SDKContentPartCodec.TEXT, SDKContentPartCodec.TEXT: part.value}
        if part.kind == ContentKind.IMAGE:
            return {
                SDKContentPartCodec.TYPE: SDKContentPartCodec.IMAGE_URL,
                SDKContentPartCodec.IMAGE_URL: {SDKContentPartCodec.URL: part.value},
            }
        raise ValueError(SDKContentPartCodec.MISSING_STATE)


class SDKMessageCodec:
    CONTENT_MISSING = "content_missing"
    EMPTY_CALLS = "empty_calls"
    NAME_NULL = "name_null"
    SHAPE_PRESENT = True
    SHAPE_ABSENT = False
    UNSPECIFIED = None
    MISSING_ROLE = "SDK message requires its role"
    INVALID_CALLS = "SDK Tool calls must be an array or null"
    INVALID_RESULT = "SDK Tool result requires its call ID and no new calls"
    UNEXPECTED_RESULT_ID = "Only Tool results may carry a result call ID"
    INVALID_MESSAGE = "SDK encoding requires ModelMessage"
    INVALID_CONTENT_PRESENCE = "SDK content presence must be boolean"
    INVALID_NAME_PRESENCE = "SDK result name presence must be boolean"
    MISSING_RESULT = "SDK result name state requires a Tool result"
    INVALID_EMPTY_CALLS = "SDK empty-call state changed"

    @classmethod
    def decode(cls, value):
        value = SDKWire.object(value)
        if SDKMessage.ROLE not in value:
            raise ValueError(SDKMessageCodec.MISSING_ROLE)
        role = ModelRole(value[SDKMessage.ROLE])
        content = value.get(SDKMessage.CONTENT)
        extras = SDKWire.extras(value, SDKMessage.FIELDS)
        if SDKMessage.NAME in value and role != ModelRole.TOOL:
            extras[SDKMessage.NAME] = value[SDKMessage.NAME]
        calls = value.get(SDKMessage.TOOL_CALLS)
        if calls is not SDKMessageCodec.UNSPECIFIED and not isinstance(calls, list):
            raise TypeError(SDKMessageCodec.INVALID_CALLS)
        decoded = []
        for call in [] if calls is SDKMessageCodec.UNSPECIFIED else calls:
            decoded.append(SDKToolCodec.decode_call(call))
        if role == ModelRole.TOOL:
            if decoded or SDKMessage.TOOL_CALL_ID not in value:
                raise ValueError(SDKMessageCodec.INVALID_RESULT)
            shape = {}
            if SDKMessage.TOOL_CALLS in value:
                shape[SDKMessageCodec.EMPTY_CALLS] = calls
            if SDKMessage.NAME in value and value[SDKMessage.NAME] is SDKMessageCodec.UNSPECIFIED:
                shape[SDKMessageCodec.NAME_NULL] = SDKMessageCodec.SHAPE_PRESENT
            return ModelMessage(
                role,
                result=ToolResult(value[SDKMessage.TOOL_CALL_ID], content, value.get(SDKMessage.NAME)),
                opaque={
                    **({SDKWire.STATE: extras} if extras else {}),
                    **({SDKWire.SHAPE: shape} if shape else {}),
                },
            )
        if SDKMessage.TOOL_CALL_ID in value:
            raise ValueError(SDKMessageCodec.UNEXPECTED_RESULT_ID)
        if isinstance(content, list):
            content = SDKContentPartCodec.decode_many(content)
        shape = {}
        if SDKMessage.CONTENT not in value:
            shape[SDKMessageCodec.CONTENT_MISSING] = SDKMessageCodec.SHAPE_PRESENT
        if SDKMessage.TOOL_CALLS in value and calls in (SDKMessageCodec.UNSPECIFIED, []):
            shape[SDKMessageCodec.EMPTY_CALLS] = calls
        return ModelMessage(
            role,
            content,
            tuple(decoded),
            opaque={
                **({SDKWire.STATE: extras} if extras else {}),
                **({SDKWire.SHAPE: shape} if shape else {}),
            },
        )

    @classmethod
    def encode(cls, message):
        if not isinstance(message, ModelMessage):
            raise TypeError(SDKMessageCodec.INVALID_MESSAGE)
        message = copy_model(message)
        reserved = (
            SDKMessage.FIELDS if message.role == ModelRole.TOOL else SDKMessage.FIELDS - {SDKMessage.NAME}
        )
        value = SDKWire.extension(SDKWire.state(message.opaque), reserved)
        content = (
            [SDKContentPartCodec.encode(part) for part in message.content]
            if isinstance(message.content, tuple)
            else message.content
        )
        value.update({SDKMessage.ROLE: message.role.value, SDKMessage.CONTENT: content})
        if message.calls:
            value[SDKMessage.TOOL_CALLS] = [SDKToolCodec.encode_call(call) for call in message.calls]
        if message.result is not SDKMessageCodec.UNSPECIFIED:
            value.update(
                {SDKMessage.CONTENT: message.result.content, SDKMessage.TOOL_CALL_ID: message.result.call_id}
            )
            if message.result.name is not SDKMessageCodec.UNSPECIFIED:
                value[SDKMessage.NAME] = message.result.name
        shape = SDKWire.state(message.opaque, SDKWire.SHAPE)
        missing = shape.get(SDKMessageCodec.CONTENT_MISSING, SDKMessageCodec.SHAPE_ABSENT)
        if type(missing) is not bool:
            raise TypeError(SDKMessageCodec.INVALID_CONTENT_PRESENCE)
        if (
            missing
            and message.content is SDKMessageCodec.UNSPECIFIED
            and message.result is SDKMessageCodec.UNSPECIFIED
        ):
            value.pop(SDKMessage.CONTENT, SDKMessageCodec.UNSPECIFIED)
        null_name = shape.get(SDKMessageCodec.NAME_NULL, SDKMessageCodec.SHAPE_ABSENT)
        if type(null_name) is not bool:
            raise TypeError(SDKMessageCodec.INVALID_NAME_PRESENCE)
        if null_name:
            if message.result is SDKMessageCodec.UNSPECIFIED:
                raise ValueError(SDKMessageCodec.MISSING_RESULT)
            if message.result.name is SDKMessageCodec.UNSPECIFIED:
                value[SDKMessage.NAME] = SDKMessageCodec.UNSPECIFIED
        if SDKMessageCodec.EMPTY_CALLS in shape and not message.calls:
            if (
                shape[SDKMessageCodec.EMPTY_CALLS] is not SDKMessageCodec.UNSPECIFIED
                and shape[SDKMessageCodec.EMPTY_CALLS] != []
            ):
                raise ValueError(SDKMessageCodec.INVALID_EMPTY_CALLS)
            value[SDKMessage.TOOL_CALLS] = shape[SDKMessageCodec.EMPTY_CALLS]
        return value


class SDKRequestCodec:
    UNSPECIFIED = None
    INVALID_REQUEST = "SDK encoding requires ModelRequest"
    CONFLICTING_TOOL_MODE = "Common and SDK Tool selection cannot conflict"

    @classmethod
    def encode(cls, request):
        if not isinstance(request, ModelRequest):
            raise TypeError(SDKRequestCodec.INVALID_REQUEST)
        request = copy_model(request)
        result = {
            **request.parameters,
            LiteLLMParameters.MESSAGES: [SDKMessageCodec.encode(message) for message in request.messages],
        }
        if request.tools:
            result[LiteLLMParameters.TOOLS] = [SDKToolCodec.encode_definition(tool) for tool in request.tools]
        if request.tool_mode is not SDKRequestCodec.UNSPECIFIED:
            if LiteLLMParameters.TOOL_CHOICE in result:
                raise ValueError(SDKRequestCodec.CONFLICTING_TOOL_MODE)
            result[LiteLLMParameters.TOOL_CHOICE] = request.tool_mode.value
        return result


_STATE = SDKWire.STATE
_SHAPE = SDKWire.SHAPE
_MESSAGE_FIELDS = SDKMessage.FIELDS
_CALL_FIELDS = SDKToolCodec.CALL_FIELDS
_FUNCTION_FIELDS = SDKToolCodec.FUNCTION_FIELDS
_DEFINITION_FIELDS = SDKToolCodec.DEFINITION_FIELDS
_CALL_STATE_FIELDS = SDKToolCodec.CALL_STATE_FIELDS
_DEFINITION_STATE_FIELDS = SDKToolCodec.DEFINITION_STATE_FIELDS
_STOP_REASONS = SDKFinishReason.NORMALIZED


def _object(value):
    return SDKWire.object(value)


def _state(value, key=SDKWire.STATE):
    return SDKWire.state(value, key)


def _extras(value, reserved):
    return SDKWire.extras(value, reserved)


def _extension(value, reserved):
    return SDKWire.extension(value, reserved)


def _arguments(text):
    return SDKWire.arguments(text)


def _canonical(value):
    return SDKWire.canonical(value)


def normalize_stop_reason(reason):
    return SDKFinishReason.normalize(reason)


def _encode_call(call):
    return SDKToolCodec.encode_call(call)


def decode_tool_definition(value):
    return SDKToolCodec.decode_definition(value)


def encode_tool_definition(definition):
    return SDKToolCodec.encode_definition(definition)


def _encode_part(part):
    return SDKContentPartCodec.encode(part)


def decode_message(value):
    return SDKMessageCodec.decode(value)


def encode_message(message):
    return SDKMessageCodec.encode(message)


def encode_request(request):
    return SDKRequestCodec.encode(request)
