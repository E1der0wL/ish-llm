"""Actual SDK boundary for nonstreaming inference; domain consumers use common values."""

from typing import Protocol

from llm.core.model_protocol import (
    EmbeddingResult,
    ModelOperation,
    ModelRequest,
    ModelResponse,
    RerankResult,
)
from .wire_codec import encode_request
from .request_values import LiteLLMParameters, ModelParameters, SDKOperation
from .response_values import (
    SDKOperationResponse,
    SDKCompletionResponse,
    SDKEmbeddingResponse,
    SDKRerankResponse,
)


class CompletionFormat:
    """OperationAdapter's optional completion intent, independent of SDK wire."""

    JSON = "json"
    TEXT = "text"
    INVALID = "Unknown output format intent"


def _opaque(raw, fields):
    return SDKOperationResponse(raw).opaque(fields)


class OperationAdapterContract:
    UNSPECIFIED = None
    JSON_CONTRACT = True
    REQUIRE_MODEL = False
    AUXILIARY = True


class OperationAdapter(Protocol):
    """Own SDK options, retry/deadline, admission and ModelCall observations.

    Return common values and preserve unknown usage. Never execute Tools or write
    domain state. Output format is the caller's optional json/text intent.
    """

    embedding_enforced: dict
    vision_enforced: dict

    def describe_config(self, operation: ModelOperation) -> dict: ...
    def validate_options(
        self,
        parameters: dict,
        *,
        operation: ModelOperation,
        json_contract=OperationAdapterContract.JSON_CONTRACT,
    ) -> None: ...
    def validate_vision(
        self, parameters: dict, *, require_model=OperationAdapterContract.REQUIRE_MODEL
    ) -> None: ...
    def cache_identity(self): ...
    async def complete(
        self,
        request: ModelRequest,
        options: dict,
        *,
        output_format=OperationAdapterContract.UNSPECIFIED,
        deadline=OperationAdapterContract.UNSPECIFIED,
        decide=OperationAdapterContract.UNSPECIFIED,
    ) -> ModelResponse: ...
    async def embed(
        self, inputs, parameters: dict, options: dict, *, decide=OperationAdapterContract.UNSPECIFIED
    ) -> EmbeddingResult: ...
    async def rerank(
        self,
        query,
        documents,
        parameters: dict,
        options: dict,
        *,
        decide=OperationAdapterContract.UNSPECIFIED,
    ) -> RerankResult: ...


class LiteLLMOperations:
    SDK_OPERATIONS = {
        ModelOperation.COMPLETION: SDKOperation.ASYNC_COMPLETION,
        ModelOperation.EMBEDDING: SDKOperation.ASYNC_EMBEDDING,
        ModelOperation.RERANK: SDKOperation.ASYNC_RERANK,
    }
    embedding_enforced = LiteLLMParameters.EMBEDDING_ENFORCED
    vision_enforced = LiteLLMParameters.VISION_ENFORCED
    INVALID_TRANSPORT = "SDK transport must be callable"
    MISSING_VISION_MODEL = "Missing required setting: vision.completion.model"
    INVALID_VISION_RESPONSE = "Vision requires one nonstreaming response"
    INVALID_REQUEST = "Completion requires ModelRequest"
    INVALID_NONSTREAMING = "Nonstreaming completion requires one response"

    def __init__(self, *, call=OperationAdapterContract.UNSPECIFIED):
        if call is not OperationAdapterContract.UNSPECIFIED and not callable(call):
            raise TypeError(LiteLLMOperations.INVALID_TRANSPORT)
        self.call = call

    def cache_identity(self):
        # Project binding constructs fresh default adapters. Their SDK transport
        # is identical; injected transports and custom adapters keep instance identity.
        owner = (
            type(self)
            if type(self) is LiteLLMOperations and self.call is OperationAdapterContract.UNSPECIFIED
            else self
        )
        return (id(owner), id(self.call))

    @classmethod
    def describe_config(cls, operation=ModelOperation.COMPLETION):
        from .schema import model_schema

        return model_schema(cls.SDK_OPERATIONS[ModelOperation(operation)])

    @classmethod
    def validate_options(cls, parameters, *, operation, json_contract=OperationAdapterContract.JSON_CONTRACT):
        from .schema import validate_model_params

        validate_model_params(
            parameters, operation=cls.SDK_OPERATIONS[ModelOperation(operation)], json_contract=json_contract
        )

    @staticmethod
    def validate_vision(parameters, *, require_model=OperationAdapterContract.REQUIRE_MODEL):
        from .litellm import LiteLLMCompletionAdapter

        LiteLLMCompletionAdapter.validate_input_ownership(
            parameters, auxiliary=OperationAdapterContract.AUXILIARY
        )
        if require_model and (
            not isinstance(ModelParameters(parameters).optional_model, str)
            or not ModelParameters(parameters).model.strip()
        ):
            raise ValueError(LiteLLMOperations.MISSING_VISION_MODEL)
        if LiteLLMParameters(parameters).invalid_nonstreaming_single:
            raise ValueError(LiteLLMOperations.INVALID_VISION_RESPONSE)

    async def _invoke_sdk(
        self,
        operation,
        request,
        options,
        *,
        deadline=OperationAdapterContract.UNSPECIFIED,
        decide=OperationAdapterContract.UNSPECIFIED,
    ):
        from .requests import request_model

        return await request_model(
            self.SDK_OPERATIONS[operation],
            request,
            options,
            call=self.call,
            deadline=deadline,
            decide=decide,
            changeable=LiteLLMParameters.RETRY_CHANGEABLE,
        )

    async def complete(
        self,
        request,
        options,
        *,
        output_format=OperationAdapterContract.UNSPECIFIED,
        deadline=OperationAdapterContract.UNSPECIFIED,
        decide=OperationAdapterContract.UNSPECIFIED,
    ):
        if not isinstance(request, ModelRequest):
            raise TypeError(LiteLLMOperations.INVALID_REQUEST)
        request.validate_exchanges()
        parameters = encode_request(request)
        if LiteLLMParameters(parameters).invalid_nonstreaming_single:
            raise ValueError(LiteLLMOperations.INVALID_NONSTREAMING)
        LiteLLMParameters(parameters).select_nonstreaming()
        if output_format == CompletionFormat.JSON:
            LiteLLMParameters(parameters).select_json_format()
        elif output_format == CompletionFormat.TEXT:
            LiteLLMParameters(parameters).clear_response_format()
        elif output_format is not OperationAdapterContract.UNSPECIFIED:
            raise ValueError(CompletionFormat.INVALID)
        raw = await self._invoke_sdk(
            ModelOperation.COMPLETION, parameters, options, deadline=deadline, decide=decide
        )
        return SDKCompletionResponse(raw).to_model_response(parameters)

    async def embed(self, inputs, parameters, options, *, decide=OperationAdapterContract.UNSPECIFIED):
        request = LiteLLMParameters(parameters).embedding_request(inputs)
        raw = await self._invoke_sdk(ModelOperation.EMBEDDING, request, options, decide=decide)
        return SDKEmbeddingResponse(raw).to_result(parameters)

    async def rerank(
        self, query, documents, parameters, options, *, decide=OperationAdapterContract.UNSPECIFIED
    ):
        request = LiteLLMParameters(parameters).rerank_request(query, documents)
        raw = await self._invoke_sdk(ModelOperation.RERANK, request, options, decide=decide)
        return SDKRerankResponse(raw).to_result(documents)
