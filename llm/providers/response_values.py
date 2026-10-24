"""LiteLLM response projections; optional wire fields are not persisted evidence."""

from .embeddings import value
from llm.core.model_protocol import ModelRole, ModelResponse, EmbeddingResult, RankedDocument, RerankResult
from .request_values import ModelParameters, EmbeddingParameters


class SDKMessage:
    ROLE = "role"
    CONTENT = "content"
    TOOL_CALLS = "tool_calls"
    TOOL_CALL_ID = "tool_call_id"
    NAME = "name"
    FIELDS = frozenset((ROLE, CONTENT, TOOL_CALLS, TOOL_CALL_ID, NAME))
    DEFAULT_ROLE = ModelRole.ASSISTANT.value
    UNSPECIFIED = None

    def __init__(self, message):
        self._value = message

    @property
    def content(self):
        return value(self._value, self.CONTENT)

    @classmethod
    def completion_wire(cls, message):
        if not isinstance(message, dict):
            # SDK objects without model_dump retain their original attribute contract.
            return {
                cls.ROLE: value(message, cls.ROLE, cls.DEFAULT_ROLE),
                cls.CONTENT: value(message, cls.CONTENT),
                **(
                    {cls.TOOL_CALLS: value(message, cls.TOOL_CALLS)}
                    if value(message, cls.TOOL_CALLS) is not cls.UNSPECIFIED
                    else {}
                ),
            }
        return {cls.ROLE: cls.DEFAULT_ROLE, **message}


class SDKOperationResponse:
    USAGE = "usage"
    MODEL = "model"
    ID = "id"
    UNSPECIFIED = None
    INVALID_OBJECT = "SDK operation response requires an object"

    def __init__(self, response):
        self._value = response

    def opaque(self, fields):
        from .litellm import _sdk_mapping
        from .wire_codec import SDKWire

        projected = _sdk_mapping(self._value)
        if not isinstance(projected, dict):
            raise TypeError(self.INVALID_OBJECT)
        extra = {key: item for key, item in projected.items() if key not in fields}
        return {SDKWire.STATE: extra} if extra else {}


class SDKCompletionResponse(SDKOperationResponse):
    CHOICES = "choices"
    MESSAGE = "message"
    FINISH_REASON = "finish_reason"
    SINGLE_CHOICE = 1
    INVALID_CHOICES = "Expected one completion choice"

    @property
    def choices(self):
        return value(self._value, self.CHOICES)

    @classmethod
    def message(cls, choice):
        return SDKMessage(value(choice, cls.MESSAGE))

    def to_model_response(self, parameters):
        from .litellm import _usage, _sdk_mapping
        from .wire_codec import decode_message, normalize_stop_reason

        choices = self.choices
        if not isinstance(choices, list) or len(choices) != self.SINGLE_CHOICE:
            raise ValueError(self.INVALID_CHOICES)
        choice = choices[0]
        message = _sdk_mapping(value(choice, self.MESSAGE))
        message = SDKMessage.completion_wire(message)
        reason = value(choice, self.FINISH_REASON)
        model = value(self._value, self.MODEL)
        return ModelResponse(
            decode_message(message),
            normalize_stop_reason(reason),
            _usage(value(self._value, self.USAGE)) or self.UNSPECIFIED,
            value(self._value, self.ID),
            ModelParameters(parameters).optional_model if model is self.UNSPECIFIED else model,
            reason,
        )


class SDKEmbeddingResponse(SDKOperationResponse):
    def to_result(self, parameters):
        from .litellm import _usage
        from .embeddings import extract_single_embedding, SingleEmbeddingWire

        vector = extract_single_embedding(
            self._value, dimensions=EmbeddingParameters(parameters).optional_dimensions
        )
        return EmbeddingResult(
            tuple(vector),
            _usage(value(self._value, self.USAGE)) or self.UNSPECIFIED,
            self.opaque({SingleEmbeddingWire.DATA, self.USAGE}),
        )


class SDKRerankResponse(SDKOperationResponse):
    RESULTS = "results"
    INDEX = "index"
    RELEVANCE_SCORE = "relevance_score"
    INVALID_RESULTS = "Expected ranking results"
    INVALID_INDEX = "Ranking index is outside the supplied documents"

    def to_result(self, documents):
        from .litellm import _usage

        rows = value(self._value, self.RESULTS)
        if not isinstance(rows, list):
            raise ValueError(self.INVALID_RESULTS)
        result = RerankResult(
            tuple(RankedDocument(value(row, self.INDEX), value(row, self.RELEVANCE_SCORE)) for row in rows),
            _usage(value(self._value, self.USAGE)) or self.UNSPECIFIED,
            self.opaque({self.RESULTS, self.USAGE}),
        )
        if any(row.index >= len(documents) for row in result.rows):
            raise ValueError(self.INVALID_INDEX)
        return result
