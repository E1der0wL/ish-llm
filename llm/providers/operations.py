"""Nonstreaming operation adapters. SDK envelopes never reach RAG/Vision logic."""
from typing import Protocol

from llm.core.model_protocol import ModelRequest, ModelResponse, EmbeddingResult, RankedDocument, RerankResult
from .wire_codec import encode_request, decode_message, normalize_stop_reason
from .embeddings import value, extract_single_embedding


class OperationAdapter(Protocol):
    """Trusted host implementation for nonstream operations.

    Own SDK encoding, validation, retry/deadline, ProviderCalls admission and
    ModelCall observation. Return normalized values with unknown usage as None.
    Never execute tools or write domain state. Output format is None/json/text;
    it is an intent that each adapter translates to its own capabilities.
    """
    vision_enforced: dict
    def describe_config(self, operation='acompletion') -> dict: ...
    def validate_options(self, parameters, *, operation, json_contract=True) -> None: ...
    def validate_vision(self, parameters) -> None: ...
    async def complete(self, request: ModelRequest, options: dict, *, output_format=None,
                       deadline=None, decide=None) -> ModelResponse: ...
    async def embed(self, inputs, parameters: dict, options: dict, *, decide=None) -> EmbeddingResult: ...
    async def rerank(self, query, documents, parameters: dict, options: dict, *, decide=None) -> RerankResult: ...


class LiteLLMOperations:
    """One adapter for completion/embedding/rerank with the existing retry boundary."""
    def __init__(self, call=None):
        self.call=call

    @staticmethod
    def describe_config(operation='acompletion'):
        from .schema import model_schema
        return model_schema(operation)

    @staticmethod
    def validate_options(parameters, *, operation, json_contract=True):
        from .schema import validate_model_params
        validate_model_params(parameters, operation=operation, json_contract=json_contract)

    vision_enforced = {'stream': False, 'n': 1}

    @staticmethod
    def validate_vision(parameters):
        if any(k in parameters for k in ('messages','tools','tool_choice','functions','function_call')):
            raise ValueError('Vision owns messages and does not execute nested tools')
        if parameters.get('stream',False) is not False or parameters.get('n',1)!=1:
            raise ValueError('Vision requires stream=False and n=1')

    async def _invoke_sdk(self, operation, request, options, *, deadline=None, decide=None):
        from .requests import request_model
        return await request_model(operation,request,options,call=self.call,deadline=deadline,
                                   decide=decide,changeable=('timeout',))

    async def complete(self, request: ModelRequest, options, *, output_format=None, deadline=None, decide=None):
        params=encode_request(request)
        if params.get('stream',False) is not False or params.get('n',1)!=1:
            raise ValueError('Nonstreaming completion requires stream=False and n=1')
        params['stream']=False
        if output_format=='json':
            params['response_format']={'type':'json_object'}
        elif output_format=='text':
            params.pop('response_format',None)
        raw=await self._invoke_sdk('acompletion',params,options,deadline=deadline,decide=decide)
        return self.completion_result(raw)

    @staticmethod
    def completion_result(raw):
        from .litellm import _usage
        choice=value(raw,'choices')[0]
        message=value(choice,'message')
        if callable(getattr(message,'model_dump',None)):
            message=message.model_dump(exclude_none=True)
        message={'role':'assistant',**message} if isinstance(message,dict) else {
            'role':'assistant','content':value(message,'content')}
        reason=value(choice,'finish_reason')
        return ModelResponse(decode_message(message),normalize_stop_reason(reason),_usage(value(raw,'usage')) or None,
                             value(raw,'id'),value(raw,'model'),reason)

    async def embed(self, inputs, parameters, options, *, decide=None):
        params={**parameters,'input':inputs,'caching':False,'cache':{'no-cache':True,'no-store':True}}
        raw=await self._invoke_sdk('aembedding',params,options,decide=decide)
        return self.embedding_result(raw,dimensions=parameters.get('dimensions'))

    @staticmethod
    def embedding_result(raw, *, dimensions=None):
        from .litellm import _usage
        return EmbeddingResult(tuple(extract_single_embedding(raw,dimensions=dimensions)),
                               _usage(value(raw,'usage')) or None)

    async def rerank(self, query, documents, parameters, options, *, decide=None):
        raw=await self._invoke_sdk('arerank',{**parameters,'query':query,'documents':documents},options,decide=decide)
        return self.rerank_result(raw,count=len(documents))

    @staticmethod
    def rerank_result(raw, *, count=None):
        import math
        from .litellm import _usage
        rows=value(raw,'results')
        if not isinstance(rows,list):
            raise ValueError('Rerank requires results')
        result=[]; seen=set()
        for row in rows:
            index,score=value(row,'index'),value(row,'relevance_score')
            if (type(index) is not int or index<0 or count is not None and index>=count or index in seen
                    or type(score) not in (int,float) or not math.isfinite(score)):
                raise ValueError('Invalid rerank result')
            seen.add(index); result.append(RankedDocument(index,score))
        return RerankResult(tuple(result),_usage(value(raw,'usage')) or None)


def image_part(raw, mime_type):
    import base64
    from llm.core.model_protocol import ContentPart
    return ContentPart('image','data:'+mime_type+';base64,'+base64.b64encode(raw).decode('ascii'),mime_type)
