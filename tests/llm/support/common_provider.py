"""A non-OpenAI fake transport. It never imports a LiteLLM adapter or wire codec."""
from copy import deepcopy
import json
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelResponse, StopReason, ToolCall, option_copy
from llm.providers.streaming import CompletionUpdate, CompletionError
from llm.providers.observations import ModelCall, provider_attempt
from llm.providers.calls import current_calls
from llm.core.schema import object_schema


class CommonDecoder:
    def __init__(self, **limits):
        self.limits=limits
        self.answer=None
        self.size=0

    def decode(self, envelope):
        if self.answer is not None:
            raise CompletionError('Envelope after seal')
        if 'fragment' in envelope:
            self.size += len(envelope['fragment'])
            maximum=self.limits['max_output_chars']
            if maximum is not None and self.size > maximum:
                raise CompletionError('Output exceeded',code='completion_output_limit')
            yield CompletionUpdate(text=envelope['fragment'])
        if 'sealed' in envelope:
            body=envelope['sealed']
            calls=tuple(ToolCall(c['identity'],c['operation'],c['input']) for c in body.get('invoke',()))
            if self.limits['max_tool_calls'] is not None and len(calls)>self.limits['max_tool_calls']:
                raise CompletionError('Too many tools',code='completion_tool_limit')
            limit = self.limits['max_argument_chars']
            if limit is not None and any(len(json.dumps(c.arguments)) > limit for c in calls):
                raise CompletionError('Arguments exceeded', code='completion_argument_limit')
            self.answer=ModelResponse(ModelMessage('assistant',body.get('say'),calls,
                opaque={'fake':{'signature':body.get('signature','signed')}}),
                StopReason.TOOLS if calls else StopReason.COMPLETE,body.get('spent'))
            yield CompletionUpdate(usage=body.get('spent'),finish_reason='fake-sealed',termination=self.answer.stop)

    def finish(self):
        if self.answer is None:
            raise CompletionError('Missing seal',code='completion_incomplete')
        return self.answer


class CommonProvider:
    execution_binding={'protocol':'fake-v1'}

    def __init__(self,*attempts):
        self.attempts=list(attempts)
        self.requests=[]
        self.closed=0

    def describe_config(self):
        return object_schema({'model':{'type':'string'}})

    def validate_request(self,request,*,require_model=False):
        if require_model and not request.get('model'):
            raise ValueError('Model required')

    def validate_processing(self,request,*,original):
        if request!=original:
            raise ValueError('Fake transport settings are immutable')

    def validate_input_ownership(self, parameters, *, auxiliary=False):
        if set(parameters) & {'messages', 'tools'}:
            raise ValueError('Use common message and Tool fields')

    def auxiliary_parameters(self, parameters):
        return deepcopy(parameters)

    def prepare(self,request):
        if not isinstance(request,ModelRequest):
            raise TypeError('Common request required')
        request.validate_exchanges()
        return request

    def describe_call(self,request):
        return ModelCall('fake',request.parameters.get('model'),lambda counter: counter(request),
                         lambda response: response.usage or {},self.classify_error)

    def describe_request(self,request):
        return {'model':request.parameters.get('model')}

    def effective_attempts(self,request,maximum):
        return maximum

    def classify_error(self,error):
        return 'provider_connection' if isinstance(error,ConnectionError) else 'provider_failed'

    def retryable(self,error):
        return isinstance(error,ConnectionError)

    def retry_fields(self,request):
        return ()

    def validate_retry(self,request,*,original):
        if request != original:
            raise ValueError('Request changed')

    def decoder(self,**limits):
        return CommonDecoder(**limits)

    async def stream(self,request,*,buffer_size):
        calls = current_calls()
        if calls is not None:
            await calls.acquire()
        self.requests.append(option_copy(request))
        try:
            with provider_attempt(self.describe_call(request)):
                for envelope in self.attempts.pop(0):
                    if isinstance(envelope,BaseException):
                        raise envelope
                    yield envelope
        finally:
            self.closed+=1
            if calls is not None:
                calls.release()
