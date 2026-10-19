"""LiteLLM/OpenAI wire translation and explicit pre-protocol compatibility.

The legacy projections are also the frozen v1 worker/Graph binding encoding.
They are not the representation used by the model execution pipeline.
"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
import json

from llm.core.model_protocol import (
    ContentPart, ModelMessage, ModelRequest, ModelResponse, StopReason,
    ToolCall, ToolDefinition, ToolResult, option_copy,
)


def stop_reason(reason):
    return {'stop': StopReason.COMPLETE, 'tool_calls': StopReason.TOOLS,
            'length': StopReason.OUTPUT_LIMIT, 'content_filter': StopReason.BLOCKED}.get(reason, StopReason.UNKNOWN)


def message_from_legacy(value):
    if isinstance(value, ModelMessage):
        return deepcopy(value)
    if not isinstance(value, Mapping):
        raise TypeError('Legacy model message must be a mapping')
    if value.get('version') == 1:
        return ModelMessage.from_dict(dict(value))
    role, content = value.get('role'), value.get('content')
    opaque = {k: deepcopy(v) for k, v in value.items() if k not in ('role', 'content', 'tool_calls', 'tool_call_id', 'name')}
    if 'name' in value and role != 'tool':
        opaque['name'] = value['name']
    calls=[]
    for call in value.get('tool_calls') or ():
        function=call['function']
        raw=function['arguments']
        arguments=json.loads(raw, parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Non-finite Tool argument')))
        state={'arguments_text':raw, 'extra': {k:deepcopy(v) for k,v in call.items() if k not in ('id','type','function')},
               'function_extra': {k:deepcopy(v) for k,v in function.items() if k not in ('name','arguments')}}
        calls.append(ToolCall(call['id'],function['name'],arguments,{'litellm':state}))
    if role == 'tool':
        return ModelMessage(role, result=ToolResult(value['tool_call_id'], content, value.get('name')),
                            opaque={'litellm':opaque} if opaque else {})
    if isinstance(content, list):
        parts=[]
        for part in content:
            if part.get('type') == 'text':
                parts.append(ContentPart('text',part['text'],opaque={'litellm':deepcopy(part)}))
            elif part.get('type') == 'image_url':
                parts.append(ContentPart('image',part['image_url']['url'],opaque={'litellm':deepcopy(part)}))
            else:
                parts.append(ContentPart('opaque','',opaque={'litellm':deepcopy(part)}))
        content=tuple(parts)
    return ModelMessage(role,content,tuple(calls),opaque={'litellm':opaque} if opaque else {})


def message_to_legacy(message):
    if not isinstance(message, ModelMessage):
        return deepcopy(message)
    value=deepcopy(message.opaque.get('litellm',{}))
    value.update(role=message.role,content=message.content)
    if isinstance(message.content, tuple):
        parts=[]
        for part in message.content:
            if 'litellm' in part.opaque:
                parts.append(deepcopy(part.opaque['litellm']))
            elif part.kind == 'text':
                parts.append({'type':'text','text':part.value})
            elif part.kind == 'image':
                parts.append({'type':'image_url','image_url':{'url':part.value}})
            else:
                raise ValueError('LiteLLM adapter cannot encode this content part')
        value['content']=parts
    if message.calls:
        calls=[]
        for call in message.calls:
            state=call.opaque.get('litellm',{})
            raw=state.get('arguments_text')
            if raw is None or json.loads(raw) != call.arguments:
                raw=json.dumps(call.arguments,ensure_ascii=False)
            calls.append({**deepcopy(state.get('extra',{})), 'id':call.id,'type':'function',
                'function':{**deepcopy(state.get('function_extra',{})), 'name':call.name,'arguments':raw}})
        value['tool_calls']=calls
    if message.result:
        value.update(content=message.result.content,tool_call_id=message.result.call_id)
        if message.result.name is not None:
            value['name']=message.result.name
    return value


def definition_from_legacy(value):
    if isinstance(value, ToolDefinition):
        return deepcopy(value)
    if not isinstance(value, dict) or value.get('type') != 'function' or not isinstance(value.get('function'),dict):
        raise ValueError('Invalid legacy Tool definition')
    fn=value['function']
    return ToolDefinition(fn['name'],fn.get('description',''),fn['parameters'],fn.get('strict'),
        {'litellm':{'outer':{k:deepcopy(v) for k,v in value.items() if k not in ('type','function')},
                    'function':{k:deepcopy(v) for k,v in fn.items() if k not in ('name','description','parameters','strict')}}})


def definition_to_legacy(value):
    state=value.opaque.get('litellm',{})
    return {**deepcopy(state.get('outer',{})), 'type':'function', 'function':{
        **deepcopy(state.get('function',{})), 'name':value.name,'description':value.description,
        'parameters':deepcopy(value.parameters),**({'strict':value.strict} if value.strict is not None else {})}}


def request_from_legacy(value):
    if isinstance(value, ModelRequest):
        return option_copy(value)
    parameters=option_copy(dict(value))
    messages=[message_from_legacy(m) for m in parameters.pop('messages',[])]
    tools=tuple(definition_from_legacy(t) for t in parameters.pop('tools',[]) or ())
    return ModelRequest(messages,parameters,tools)


def request_to_legacy(value):
    if not isinstance(value, ModelRequest):
        return option_copy(value)
    result={**option_copy(value.parameters),'messages':[message_to_legacy(m) for m in value.messages]}
    if value.tools:
        result['tools']=[definition_to_legacy(t) for t in value.tools]
    return result


def response_from_legacy(value, **metadata):
    if isinstance(value, ModelResponse):
        return deepcopy(value)
    if isinstance(value, dict) and value.get('version') == 1 and 'message' in value:
        return ModelResponse.from_dict(value)
    return ModelResponse(message_from_legacy(value),**metadata)


def restore_response(value):
    """Read old Loop response records without rewriting or changing their binding."""
    return response_from_legacy(value)
