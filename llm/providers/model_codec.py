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
    compat = {}
    if 'content' not in value:
        compat['content_missing'] = True
    if 'tool_calls' in value and not value['tool_calls']:
        compat['empty_calls'] = deepcopy(value['tool_calls'])
    return ModelMessage(role,content,tuple(calls),opaque={**({'litellm':opaque} if opaque else {}),
        **({'litellm_compat':compat} if compat else {})})


def message_to_legacy(message):
    if not isinstance(message, ModelMessage):
        return deepcopy(message)
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
    compat = message.opaque.get('litellm_compat', {})
    if compat.get('content_missing') and message.content is None:
        value.pop('content', None)
    if 'empty_calls' in compat and not message.calls:
        value['tool_calls'] = deepcopy(compat['empty_calls'])
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
    if value.tool_mode is not None:
        result['tool_choice']=value.tool_mode
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


def adapter_request(adapter, request):
    """Compatibility for registered pre-protocol adapters; new adapters receive values."""
    if getattr(adapter, 'model_protocol', None) == 1:
        return request_from_legacy(request)
    return request_to_legacy(request)


def changed_request(request, decision, adapter):
    from llm.policies.retry import change_arguments
    if isinstance(request, ModelRequest):
        def validate(parameters):
            adapter.validate_retry(replace(request, parameters=parameters), original=request)
        parameters=change_arguments(request.parameters,decision,allowed=adapter.retry_fields(request),
                                    copy=option_copy,validate=validate)
        return replace(request,parameters=parameters)
    return change_arguments(request,decision,allowed=adapter.retry_fields(request),copy=option_copy,
                            validate=lambda value: adapter.validate_retry(value,original=request))


def counter_input(counter, request):
    """Old host counters are kwargs consumers; marked counters accept common requests."""
    return request_from_legacy(request) if getattr(counter, 'model_protocol', None) == 1 else request_to_legacy(request)


def validate_input_ownership(parameters, *, adapter=None, auxiliary=False):
    """Legacy SDK argument reservations; adapters may add their own input namespace."""
    validate=getattr(adapter,'validate_input_ownership',None)
    if callable(validate):
        return validate(parameters,auxiliary=auxiliary)
    fields=('messages','tools','functions','function_call') + (('tool_choice',) if auxiliary else ())
    if not isinstance(parameters,dict) or any(k in parameters for k in fields):
        raise ValueError('Model messages and registered tools are owned by the caller')


def validate_auxiliary_options(parameters, *, adapter=None):
    validate_input_ownership(parameters,adapter=adapter,auxiliary=True)
    if adapter is not None:
        adapter.validate_request(parameters)
    elif parameters.get('stream',True) is not True or parameters.get('n',1)!=1:
        raise ValueError('Auxiliary completion requires stream=True and n=1')


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return deepcopy(value)


class _LegacyMutation:
    def _wrap(self, value):
        if isinstance(value, dict):
            return _LegacyDict(value, self._root)
        if isinstance(value, list):
            return _LegacyList(value, self._root)
        return deepcopy(value)

    def _change(self, method, *args, **kwargs):
        before = _plain(self._root)
        try:
            result = method(*args, **kwargs)
            self._root._publish()
            return result
        except BaseException:
            self._root._reset(before)
            raise

    def __deepcopy__(self, memo):
        return _plain(self)


class _LegacyDict(_LegacyMutation, dict):
    def __init__(self, value, root):
        self._root = root
        dict.__init__(self, ((k, self._wrap(v)) for k, v in value.items()))

    def _reset(self, value):
        dict.clear(self)
        dict.update(self, ((k, self._wrap(v)) for k, v in value.items()))

    def __setitem__(self, key, value):
        self._change(dict.__setitem__, self, key, self._wrap(value))

    def __delitem__(self, key):
        self._change(dict.__delitem__, self, key)

    def update(self, *args, **kwargs):
        self._change(dict.update, self, {k: self._wrap(v) for k, v in dict(*args, **kwargs).items()})

    def pop(self, *args):
        return self._change(dict.pop, self, *args)

    def popitem(self):
        return self._change(dict.popitem, self)

    def clear(self):
        self._change(dict.clear, self)

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def __ior__(self, other):
        self.update(other)
        return self


class _LegacyList(_LegacyMutation, list):
    def __init__(self, value, root):
        self._root = root
        list.__init__(self, (self._wrap(v) for v in value))

    def __setitem__(self, key, value):
        value = [self._wrap(v) for v in value] if isinstance(key, slice) else self._wrap(value)
        self._change(list.__setitem__, self, key, value)

    def __delitem__(self, key):
        self._change(list.__delitem__, self, key)

    def append(self, value):
        self._change(list.append, self, self._wrap(value))

    def extend(self, values):
        self._change(list.extend, self, [self._wrap(v) for v in values])

    def insert(self, index, value):
        self._change(list.insert, self, index, self._wrap(value))

    def pop(self, *args):
        return self._change(list.pop, self, *args)

    def remove(self, value):
        self._change(list.remove, self, value)

    def clear(self):
        self._change(list.clear, self)

    def reverse(self):
        self._change(list.reverse, self)

    def sort(self, *args, **kwargs):
        self._change(list.sort, self, *args, **kwargs)

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __imul__(self, count):
        self._change(list.__imul__, self, count)
        return self


class LegacyMessageView(_LegacyDict):
    """Mutable v1 processor projection; validated changes update the common owner.

    Engine code uses common values. This boundary preserves nested legacy edits
    and other adapters' opaque state without interpreting their contents.
    """
    def __init__(self, owner):
        self.owner = owner
        super().__init__(message_to_legacy(owner.message), self)

    def _publish(self):
        previous = self.owner.message
        message = message_from_legacy(_plain(self))
        opaque = {k: deepcopy(v) for k, v in previous.opaque.items() if k != 'litellm'}
        calls = {c.id: c for c in previous.calls}
        message = replace(message, opaque={**opaque, **message.opaque}, calls=tuple(
            replace(c, opaque={**({k: deepcopy(v) for k, v in calls[c.id].opaque.items() if k != 'litellm'}
                                 if c.id in calls else {}), **c.opaque}) for c in message.calls))
        object.__setattr__(self.owner, 'message', message)


def diagnostic_termination(details):
    """Read v1 diagnostic observations without rewriting persisted records."""
    return details.get('termination') or stop_reason(details.get('finish_reason'))


def response_projection(response):
    """Frozen pre-protocol BaseEngine response dictionary (reasoning is observed separately)."""
    value = message_to_legacy(response.message)
    value.pop('reasoning_content', None)
    return value


def validate_default_processing(parameters, *, original):
    """Compatibility for standalone v1 pipelines without a selected adapter."""
    from .litellm import LiteLLMCompletionAdapter
    LiteLLMCompletionAdapter().validate_processing(parameters, original=original)
