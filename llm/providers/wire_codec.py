"""LiteLLM wire conversion. Persistent common records use their own strict readers."""

from collections.abc import Mapping
import json

from llm.core.model_protocol import (
    ContentKind, ContentPart, ModelMessage, ModelRequest, ModelRole, StopReason,
    ToolCall, ToolDefinition, ToolResult, copy_model, json_copy,
)


_STATE = "litellm"
_SHAPE = "litellm_wire"
_MESSAGE_FIELDS = frozenset(("role", "content", "tool_calls", "tool_call_id", "name"))
_CALL_FIELDS = frozenset(("id", "type", "function"))
_FUNCTION_FIELDS = frozenset(("name", "arguments"))
_DEFINITION_FIELDS = frozenset(("name", "description", "parameters", "strict"))
_CALL_STATE_FIELDS = frozenset(("arguments_text", "type_missing", "extra", "function_extra"))
_DEFINITION_STATE_FIELDS = frozenset(("description_missing", "strict_present", "outer", "function"))
_STOP_REASONS = {"stop": StopReason.COMPLETE, "tool_calls": StopReason.TOOLS,
                 "length": StopReason.OUTPUT_LIMIT, "content_filter": StopReason.BLOCKED}


def normalize_stop_reason(reason):
    if reason is not None and not isinstance(reason, str):
        raise TypeError("SDK finish reason must be text or None")
    return _STOP_REASONS.get(reason, StopReason.UNKNOWN)


def _object(value):
    if not isinstance(value, Mapping):
        raise TypeError("SDK value must be an object")
    return json_copy(dict(value))


def _state(value, key=_STATE):
    return _object(value[key]) if key in value else {}


def _extras(value, reserved):
    return {key: item for key, item in value.items() if key not in reserved}


def _extension(value, reserved):
    value = _object(value)
    if reserved.intersection(value):
        raise ValueError("Opaque extensions cannot replace common model fields")
    return value


def _arguments(text):
    if not isinstance(text, str):
        raise TypeError("SDK Tool arguments require completed JSON text")

    def reject_constant(value):
        raise ValueError("Tool arguments must be finite JSON")

    return _object(json.loads(text, parse_constant=reject_constant))


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode_message(value):
    value = _object(value)
    if "role" not in value:
        raise ValueError("SDK message requires its role")
    role = ModelRole(value["role"])
    content = value.get("content")
    extras = _extras(value, _MESSAGE_FIELDS)
    if "name" in value and role != ModelRole.TOOL:
        extras["name"] = value["name"]
    calls = value.get("tool_calls")
    if calls is not None and not isinstance(calls, list):
        raise TypeError("SDK Tool calls must be an array or null")
    decoded = []
    for call in [] if calls is None else calls:
        call = _object(call)
        if "type" in call and call["type"] != "function":
            raise ValueError("Unsupported SDK Tool call type")
        function = _object(call.get("function"))
        if not {"id"} <= call.keys() or not {"name", "arguments"} <= function.keys():
            raise ValueError("Incomplete SDK Tool call")
        arguments = _arguments(function["arguments"])
        state = {"arguments_text": function["arguments"], "type_missing": "type" not in call,
                 "extra": _extras(call, _CALL_FIELDS), "function_extra": _extras(function, _FUNCTION_FIELDS)}
        decoded.append(ToolCall(call["id"], function["name"], arguments, {_STATE: state}))
    if role == ModelRole.TOOL:
        if decoded or "tool_call_id" not in value:
            raise ValueError("SDK Tool result requires its call ID and no new calls")
        shape = {}
        if "tool_calls" in value:
            shape["empty_calls"] = calls
        if "name" in value and value["name"] is None:
            shape["name_null"] = True
        return ModelMessage(role, result=ToolResult(value["tool_call_id"], content, value.get("name")),
                            opaque={**({_STATE: extras} if extras else {}), **({_SHAPE: shape} if shape else {})})
    if "tool_call_id" in value:
        raise ValueError("Only Tool results may carry a result call ID")
    if isinstance(content, list):
        parts = []
        for part in content:
            part = _object(part)
            if not isinstance(part.get("type"), str) or not part["type"]:
                raise ValueError("SDK content part requires its type")
            if part.get("type") == "text":
                parts.append(ContentPart(ContentKind.TEXT, part.get("text"), opaque={_STATE: part}))
            elif part.get("type") == "image_url":
                image = _object(part.get("image_url"))
                parts.append(ContentPart(ContentKind.IMAGE, image.get("url"), opaque={_STATE: part}))
            else:
                parts.append(ContentPart(ContentKind.OPAQUE, "", opaque={_STATE: part}))
        content = tuple(parts)
    shape = {}
    if "content" not in value:
        shape["content_missing"] = True
    if "tool_calls" in value and calls in (None, []):
        shape["empty_calls"] = calls
    return ModelMessage(role, content, tuple(decoded), opaque={
        **({_STATE: extras} if extras else {}), **({_SHAPE: shape} if shape else {})})


def _encode_part(part):
    if _STATE in part.opaque:
        value = _state(part.opaque)
        if part.kind == ContentKind.TEXT:
            if value.get("type") != "text" or not isinstance(value.get("text"), str):
                raise ValueError("Opaque content kind changed")
            value["text"] = part.value
        elif part.kind == ContentKind.IMAGE:
            if (value.get("type") != "image_url" or not isinstance(value.get("image_url"), dict)
                    or not isinstance(value["image_url"].get("url"), str)):
                raise ValueError("Opaque image shape changed")
            value["image_url"]["url"] = part.value
        elif part.kind != ContentKind.OPAQUE:
            raise ValueError("Unsupported opaque SDK content")
        elif not isinstance(value.get("type"), str) or not value["type"]:
            raise ValueError("Opaque SDK content requires its type")
        return value
    if part.kind == ContentKind.TEXT:
        return {"type": "text", "text": part.value}
    if part.kind == ContentKind.IMAGE:
        return {"type": "image_url", "image_url": {"url": part.value}}
    raise ValueError("This LiteLLM content kind requires its original adapter state")


def _encode_call(call):
    state = _state(call.opaque)
    if _STATE in call.opaque and state.keys() != _CALL_STATE_FIELDS:
        raise ValueError("SDK Tool state requires all current fields")
    text = state.get("arguments_text")
    if _STATE in call.opaque and not isinstance(text, str):
        raise TypeError("Original SDK Tool arguments require text")
    # Python equality conflates False/0 and 1/1.0. Preserve original wire text only
    # when its JSON types and values still match the common argument object.
    if text is None or _canonical(_arguments(text)) != _canonical(call.arguments):
        text = json.dumps(call.arguments, ensure_ascii=False, allow_nan=False)
    missing = state.get("type_missing", False)
    if type(missing) is not bool:
        raise TypeError("SDK type presence must be boolean")
    return {**_extension(state.get("extra", {}), _CALL_FIELDS), "id": call.id,
            **({} if missing else {"type": "function"}),
            "function": {**_extension(state.get("function_extra", {}), _FUNCTION_FIELDS),
                         "name": call.name, "arguments": text}}


def encode_message(message):
    if not isinstance(message, ModelMessage):
        raise TypeError("SDK encoding requires ModelMessage")
    message = copy_model(message)
    reserved = _MESSAGE_FIELDS if message.role == ModelRole.TOOL else _MESSAGE_FIELDS - {"name"}
    value = _extension(_state(message.opaque), reserved)
    content = ([_encode_part(part) for part in message.content]
               if isinstance(message.content, tuple) else message.content)
    value.update(role=message.role.value, content=content)
    if message.calls:
        value["tool_calls"] = [_encode_call(call) for call in message.calls]
    if message.result is not None:
        value.update(content=message.result.content, tool_call_id=message.result.call_id)
        if message.result.name is not None:
            value["name"] = message.result.name
    shape = _state(message.opaque, _SHAPE)
    missing = shape.get("content_missing", False)
    if type(missing) is not bool:
        raise TypeError("SDK content presence must be boolean")
    if missing and message.content is None and message.result is None:
        value.pop("content", None)
    null_name = shape.get("name_null", False)
    if type(null_name) is not bool:
        raise TypeError("SDK result name presence must be boolean")
    if null_name:
        if message.result is None:
            raise ValueError("SDK result name state requires a Tool result")
        if message.result.name is None:
            value["name"] = None
    if "empty_calls" in shape and not message.calls:
        if shape["empty_calls"] is not None and shape["empty_calls"] != []:
            raise ValueError("SDK empty-call state changed")
        value["tool_calls"] = shape["empty_calls"]
    return value


def decode_tool_definition(value):
    value = _object(value)
    if value.get("type") != "function":
        raise ValueError("Unsupported SDK Tool definition")
    function = _object(value.get("function"))
    if not {"name", "parameters"} <= function.keys():
        raise ValueError("Incomplete SDK Tool definition")
    return ToolDefinition(function["name"], function.get("description", ""), function["parameters"],
        function.get("strict"), {_STATE: {"description_missing": "description" not in function,
            "strict_present": "strict" in function, "outer": _extras(value, {"type", "function"}),
            "function": _extras(function, _DEFINITION_FIELDS)}})


def encode_tool_definition(definition):
    if not isinstance(definition, ToolDefinition):
        raise TypeError("SDK encoding requires ToolDefinition")
    definition = copy_model(definition)
    state = _state(definition.opaque)
    if _STATE in definition.opaque and state.keys() != _DEFINITION_STATE_FIELDS:
        raise ValueError("SDK definition state requires all current fields")
    missing, present = state.get("description_missing", False), state.get("strict_present", False)
    if type(missing) is not bool or type(present) is not bool:
        raise TypeError("SDK definition presence must be boolean")
    function = {**_extension(state.get("function", {}), _DEFINITION_FIELDS),
                "name": definition.name, "parameters": definition.parameters}
    if not missing or definition.description:
        function["description"] = definition.description
    if present or definition.strict is not None:
        function["strict"] = definition.strict
    return {**_extension(state.get("outer", {}), {"type", "function"}), "type": "function", "function": function}


def encode_request(request):
    if not isinstance(request, ModelRequest):
        raise TypeError("SDK encoding requires ModelRequest")
    request = copy_model(request)
    result = {**request.parameters, "messages": [encode_message(message) for message in request.messages]}
    if request.tools:
        result["tools"] = [encode_tool_definition(tool) for tool in request.tools]
    if request.tool_mode is not None:
        if "tool_choice" in result:
            raise ValueError("Common and SDK Tool selection cannot conflict")
        result["tool_choice"] = request.tool_mode.value
    return result
