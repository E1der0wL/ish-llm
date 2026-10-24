"""Actual SDK boundary for nonstreaming inference; domain consumers use common values."""

from typing import Protocol

from llm.core.model_protocol import (
    EmbeddingResult, ModelOperation, ModelRequest, ModelResponse, RankedDocument, RerankResult, copy_model,
)
from .embeddings import extract_single_embedding, value, SingleEmbeddingWire
from .wire_codec import decode_message, encode_request, normalize_stop_reason


class CompletionFormat:
    """OperationAdapter's optional completion intent, independent of SDK wire."""
    JSON = "json"
    TEXT = "text"


def _opaque(raw, fields):
    from .litellm import _sdk_mapping
    value = _sdk_mapping(raw)
    if not isinstance(value, dict):
        raise TypeError("SDK operation response requires an object")
    extra = {key: item for key, item in value.items() if key not in fields}
    return {"litellm": extra} if extra else {}


class OperationAdapter(Protocol):
    """Own SDK options, retry/deadline, admission and ModelCall observations.

    Return common values and preserve unknown usage. Never execute Tools or write
    domain state. Output format is the caller's optional json/text intent.
    """

    embedding_enforced: dict
    vision_enforced: dict

    def describe_config(self, operation: ModelOperation) -> dict: ...
    def validate_options(self, parameters: dict, *, operation: ModelOperation, json_contract=True) -> None: ...
    def validate_vision(self, parameters: dict, *, require_model=False) -> None: ...
    def cache_identity(self): ...
    async def complete(self, request: ModelRequest, options: dict, *, output_format=None,
                       deadline=None, decide=None) -> ModelResponse: ...
    async def embed(self, inputs, parameters: dict, options: dict, *, decide=None) -> EmbeddingResult: ...
    async def rerank(self, query, documents, parameters: dict, options: dict, *, decide=None) -> RerankResult: ...


class LiteLLMOperations:
    SDK_OPERATIONS = {ModelOperation.COMPLETION: "acompletion", ModelOperation.EMBEDDING: "aembedding",
                      ModelOperation.RERANK: "arerank"}
    embedding_enforced = {"caching": False, "cache": {"no-cache": True, "no-store": True}}
    vision_enforced = {"stream": False, "n": 1}

    def __init__(self, *, call=None):
        if call is not None and not callable(call):
            raise TypeError("SDK transport must be callable")
        self.call = call

    def cache_identity(self):
        # Project binding constructs fresh default adapters. Their SDK transport
        # is identical; injected transports and custom adapters keep instance identity.
        owner = type(self) if type(self) is LiteLLMOperations and self.call is None else self
        return (id(owner), id(self.call))

    @classmethod
    def describe_config(cls, operation=ModelOperation.COMPLETION):
        from .schema import model_schema
        return model_schema(cls.SDK_OPERATIONS[ModelOperation(operation)])

    @classmethod
    def validate_options(cls, parameters, *, operation, json_contract=True):
        from .schema import validate_model_params
        validate_model_params(parameters, operation=cls.SDK_OPERATIONS[ModelOperation(operation)], json_contract=json_contract)

    @staticmethod
    def validate_vision(parameters, *, require_model=False):
        from .litellm import LiteLLMCompletionAdapter
        LiteLLMCompletionAdapter.validate_input_ownership(parameters, auxiliary=True)
        if require_model and (not isinstance(parameters.get("model"), str) or not parameters["model"].strip()):
            raise ValueError("Missing required setting: vision.completion.model")
        if parameters.get("stream", False) is not False or parameters.get("n", 1) != 1:
            raise ValueError("Vision requires one nonstreaming response")

    async def _invoke_sdk(self, operation, request, options, *, deadline=None, decide=None):
        from .requests import request_model
        return await request_model(self.SDK_OPERATIONS[operation], request, options, call=self.call,
                                   deadline=deadline, decide=decide, changeable=("timeout",))

    async def complete(self, request, options, *, output_format=None, deadline=None, decide=None):
        if not isinstance(request, ModelRequest):
            raise TypeError("Completion requires ModelRequest")
        request.validate_exchanges()
        parameters = encode_request(request)
        if parameters.get("stream", False) is not False or parameters.get("n", 1) != 1:
            raise ValueError("Nonstreaming completion requires one response")
        parameters["stream"] = False
        if output_format == CompletionFormat.JSON:
            parameters["response_format"] = {"type": "json_object"}
        elif output_format == CompletionFormat.TEXT:
            parameters.pop("response_format", None)
        elif output_format is not None:
            raise ValueError("Unknown output format intent")
        raw = await self._invoke_sdk(ModelOperation.COMPLETION, parameters, options, deadline=deadline, decide=decide)
        from .litellm import _usage, _sdk_mapping
        choices = value(raw, "choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Expected one completion choice")
        choice = choices[0]
        message = _sdk_mapping(value(choice, "message"))
        if not isinstance(message, dict):
            # Some SDK response classes expose attributes but no model_dump.
            message = {"role": value(message, "role", "assistant"), "content": value(message, "content"),
                       **({"tool_calls": value(message, "tool_calls")} if value(message, "tool_calls") is not None else {})}
        else:
            message = {"role": "assistant", **message}
        reason = value(choice, "finish_reason")
        model = value(raw, "model")
        return ModelResponse(decode_message(message), normalize_stop_reason(reason), _usage(value(raw, "usage")) or None,
                             value(raw, "id"), parameters.get("model") if model is None else model, reason)

    async def embed(self, inputs, parameters, options, *, decide=None):
        request = {**copy_model(parameters), "input": copy_model(inputs)}
        raw = await self._invoke_sdk(ModelOperation.EMBEDDING, request, options, decide=decide)
        from .litellm import _usage
        vector = extract_single_embedding(raw, dimensions=parameters.get("dimensions"))
        return EmbeddingResult(tuple(vector), _usage(value(raw, "usage")) or None, _opaque(raw, {SingleEmbeddingWire.DATA, "usage"}))

    async def rerank(self, query, documents, parameters, options, *, decide=None):
        request = {**copy_model(parameters), "query": query, "documents": copy_model(documents)}
        raw = await self._invoke_sdk(ModelOperation.RERANK, request, options, decide=decide)
        from .litellm import _usage
        rows = value(raw, "results")
        if not isinstance(rows, list):
            raise ValueError("Expected ranking results")
        result = RerankResult(tuple(RankedDocument(value(row, "index"), value(row, "relevance_score")) for row in rows),
                              _usage(value(raw, "usage")) or None, _opaque(raw, {"results", "usage"}))
        if any(row.index >= len(documents) for row in result.rows):
            raise ValueError("Ranking index is outside the supplied documents")
        return result
