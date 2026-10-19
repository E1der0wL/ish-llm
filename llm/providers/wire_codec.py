"""LiteLLM SDK wire encoding/decoding, confined to the Provider boundary.

Common JSON records use core model from_dict methods, never this SDK codec.
"""
from collections.abc import Mapping
from copy import deepcopy
import json
from llm.core.model_protocol import (ContentPart, ModelMessage, ModelRequest, StopReason,
    ToolCall, ToolDefinition, ToolResult, option_copy)


def normalize_stop_reason(reason):
    return {'stop': StopReason.COMPLETE, 'tool_calls': StopReason.TOOLS,
            'length': StopReason.OUTPUT_LIMIT, 'content_filter': StopReason.BLOCKED}.get(reason, StopReason.UNKNOWN)


def decode_message(value):
    if not isinstance(value, Mapping):
        raise TypeError('SDK message must be a mapping')
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
               'type_missing': 'type' not in call, 'function_extra': {k:deepcopy(v) for k,v in function.items() if k not in ('name','arguments')}}
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
    wire_state = {}
    if 'content' not in value:
        wire_state['content_missing'] = True
    if 'tool_calls' in value and not value['tool_calls']:
        wire_state['empty_calls'] = deepcopy(value['tool_calls'])
    return ModelMessage(role,content,tuple(calls),opaque={**({'litellm':opaque} if opaque else {}),
        **({'litellm_wire':wire_state} if wire_state else {})})


def encode_message(message):
    if not isinstance(message, ModelMessage):
        raise TypeError('SDK encoding requires ModelMessage')
    value=deepcopy(message.opaque.get('litellm',{}))
    value.update(role=message.role,content=message.content)
    if isinstance(message.content, tuple):
        parts=[]
        for part in message.content:
            if 'litellm' in part.opaque:
                wire = deepcopy(part.opaque['litellm'])
                if part.kind == 'text':
                    wire['text'] = part.value
                elif part.kind == 'image':
                    wire['image_url']['url'] = part.value
                parts.append(wire)
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
            calls.append({**deepcopy(state.get('extra',{})), 'id':call.id,
                **({} if state.get('type_missing') else {'type':'function'}),
                'function':{**deepcopy(state.get('function_extra',{})), 'name':call.name,'arguments':raw}})
        value['tool_calls']=calls
    if message.result:
        value.update(content=message.result.content,tool_call_id=message.result.call_id)
        if message.result.name is not None:
            value['name']=message.result.name
    wire_state = message.opaque.get('litellm_wire', {})
    if wire_state.get('content_missing') and message.content is None:
        value.pop('content', None)
    if 'empty_calls' in wire_state and not message.calls:
        value['tool_calls'] = deepcopy(wire_state['empty_calls'])
    return value


def decode_tool_definition(value):
    if not isinstance(value, dict) or value.get('type') != 'function' or not isinstance(value.get('function'),dict):
        raise ValueError('Invalid SDK Tool definition')
    fn=value['function']
    return ToolDefinition(fn['name'],fn.get('description',''),fn['parameters'],fn.get('strict'),
        {'litellm':{'description_missing': 'description' not in fn, 'strict_present': 'strict' in fn,
                    'outer':{k:deepcopy(v) for k,v in value.items() if k not in ('type','function')},
                    'function':{k:deepcopy(v) for k,v in fn.items() if k not in ('name','description','parameters','strict')}}})


def encode_tool_definition(value):
    if not isinstance(value, ToolDefinition):
        raise TypeError('SDK Tool encoding requires ToolDefinition')
    state=value.opaque.get('litellm',{})
    return {**deepcopy(state.get('outer',{})), 'type':'function', 'function':{
        **deepcopy(state.get('function',{})), 'name':value.name,
        **({} if state.get('description_missing') and not value.description else {'description':value.description}),
        'parameters':deepcopy(value.parameters),
        **({'strict':value.strict} if value.strict is not None or state.get('strict_present') else {})}}


def decode_request(value):
    if not isinstance(value, Mapping):
        raise TypeError('SDK request must be a mapping')
    parameters=option_copy(dict(value))
    messages=[decode_message(m) for m in parameters.pop('messages',[])]
    tools=tuple(decode_tool_definition(t) for t in parameters.pop('tools',[]) or ())
    choice = parameters.get('tool_choice')
    mode = parameters.pop('tool_choice') if isinstance(choice, str) else None
    return ModelRequest(messages, parameters, tools, mode)


def encode_request(value):
    if not isinstance(value, ModelRequest):
        raise TypeError('SDK encoding requires ModelRequest')
    result={**option_copy(value.parameters),'messages':[encode_message(m) for m in value.messages]}
    if value.tools:
        result['tools']=[encode_tool_definition(t) for t in value.tools]
    if value.tool_mode is not None:
        result['tool_choice']=value.tool_mode
    return result
