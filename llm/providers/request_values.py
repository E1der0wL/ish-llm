"""LiteLLM request fields and transport-copy operations; caller values stay opaque."""

from llm.core.model_protocol import copy_model


class SDKOperation:
    MODULE = "litellm"
    COMPLETION = "completion"
    ASYNC_COMPLETION = "acompletion"
    ASYNC_EMBEDDING = "aembedding"
    ASYNC_RERANK = "arerank"


class ModelParameters:
    MODEL = "model"
    NO_MODEL_NAME = ""

    def __init__(self, value):
        self._value = value

    @property
    def optional_model(self):
        return self._value.get(self.MODEL)

    @property
    def model_or_empty(self):
        return self._value.get(self.MODEL, self.NO_MODEL_NAME)

    @property
    def model(self):
        return self._value[self.MODEL]

    @property
    def has_model(self):
        return self.MODEL in self._value


class SDKRetryParameters(ModelParameters):
    """Selected SDK/client retries, distinct from the Backend RetryPolicy."""

    NUM_RETRIES = "num_retries"
    MAX_RETRIES = "max_retries"
    RETRY_POLICY = "retry_policy"
    CLIENT = "client"
    RETRY_FIELDS = (NUM_RETRIES, MAX_RETRIES)
    DISABLED = 0
    UNSPECIFIED = None
    ENABLED = True
    NOT_ENABLED = False
    SDK_MODULE = SDKOperation.MODULE

    def transport_copy(self, sdk):
        result = copy_model(self._value)
        # Do not add num_retries beside another selected policy: LiteLLM maps
        # it onto max_retries and would override the client's explicit choice.
        if (
            self.NUM_RETRIES not in result
            and self.MAX_RETRIES not in result
            and result.get(self.CLIENT) is self.UNSPECIFIED
            and result.get(self.RETRY_POLICY) is self.UNSPECIFIED
            and getattr(sdk, self.NUM_RETRIES, self.UNSPECIFIED) is self.UNSPECIFIED
        ):
            result[self.NUM_RETRIES] = self.DISABLED
        if self.MAX_RETRIES not in result:
            client = result.get(self.CLIENT)
            # None leaves injected clients without this attribute untouched.
            result[self.MAX_RETRIES] = (
                self.DISABLED
                if client is self.UNSPECIFIED
                else getattr(client, self.MAX_RETRIES, self.UNSPECIFIED)
            )
        return result

    @classmethod
    def enabled(cls, value):
        # Unknown policy types (including bool) must not enable outer retries.
        return value is not cls.UNSPECIFIED and (type(value) not in (int, float) or value > cls.DISABLED)

    def selects_retries(self, *, sdk_defaults=NOT_ENABLED):
        import sys

        if any(self.enabled(self._value.get(key)) for key in self.RETRY_FIELDS):
            return self.ENABLED
        if self._value.get(self.RETRY_POLICY) is not self.UNSPECIFIED:
            return self.ENABLED
        client = self._value.get(self.CLIENT)
        if client is not self.UNSPECIFIED and self.enabled(
            getattr(client, self.MAX_RETRIES, self.UNSPECIFIED)
        ):
            return self.ENABLED
        if sdk_defaults:
            sdk = sys.modules.get(self.SDK_MODULE)
            if sdk is not self.UNSPECIFIED and self.enabled(getattr(sdk, self.NUM_RETRIES, self.UNSPECIFIED)):
                return self.ENABLED
        return self.NOT_ENABLED


class LiteLLMParameters(SDKRetryParameters):
    """Known SDK arguments; unrecognized provider extensions remain unchanged."""

    API_KEY = "api_key"
    MESSAGES = "messages"
    API_BASE = "api_base"
    TEMPERATURE = "temperature"
    REASONING_EFFORT = "reasoning_effort"
    MAX_TOKENS = "max_tokens"
    MAX_COMPLETION_TOKENS = "max_completion_tokens"
    TOP_P = "top_p"
    SEED = "seed"
    TIMEOUT = "timeout"
    RESPONSE_FORMAT = "response_format"
    STREAM = "stream"
    N = "n"
    TOOLS = "tools"
    TOOL_CHOICE = "tool_choice"
    FUNCTIONS = "functions"
    FUNCTION_CALL = "function_call"
    CACHING = "caching"
    CACHE = "cache"
    INPUT = "input"
    QUERY = "query"
    DOCUMENTS = "documents"
    PARALLEL_TOOL_CALLS = "parallel_tool_calls"
    JSON_SCHEMA = "json_schema"
    STOP = "stop"
    CACHE_NO_READ = "no-cache"
    CACHE_NO_WRITE = "no-store"
    FORMAT_TYPE = "type"
    JSON_OBJECT = "json_object"
    NONSTREAMING = False
    STREAMING = True
    SINGLE_CHOICE = 1
    INPUT_FIELDS = (MESSAGES, TOOLS, FUNCTIONS, FUNCTION_CALL)
    PROCESSOR_FIELDS = (
        *SDKRetryParameters.RETRY_FIELDS,
        SDKRetryParameters.RETRY_POLICY,
        SDKRetryParameters.CLIENT,
        TOOLS,
        TOOL_CHOICE,
        FUNCTIONS,
        FUNCTION_CALL,
        STREAM,
        N,
    )
    AUXILIARY_FIELDS = (
        *INPUT_FIELDS,
        TOOL_CHOICE,
        PARALLEL_TOOL_CALLS,
        RESPONSE_FORMAT,
        JSON_SCHEMA,
        STOP,
        N,
        *SDKRetryParameters.RETRY_FIELDS,
    )
    OUTPUT_LIMIT_FIELDS = (MAX_TOKENS, MAX_COMPLETION_TOKENS)
    EMBEDDING_ENFORCED = {CACHING: False, CACHE: {CACHE_NO_READ: True, CACHE_NO_WRITE: True}}
    VISION_ENFORCED = {STREAM: NONSTREAMING, N: SINGLE_CHOICE}
    RETRY_CHANGEABLE = (TIMEOUT,)
    RETRY_PROTECTED = (
        STREAM,
        N,
        TOOLS,
        TOOL_CHOICE,
        FUNCTIONS,
        FUNCTION_CALL,
        SDKRetryParameters.CLIENT,
        SDKRetryParameters.NUM_RETRIES,
        SDKRetryParameters.MAX_RETRIES,
        SDKRetryParameters.RETRY_POLICY,
        CACHING,
        CACHE,
    )
    INVALID_RETRY = "Retry cannot change provider contract: {key}"
    INVALID_ACCOUNTING = "SDK accounting requires message and Tool arrays"
    INVALID_STREAM = "Completion requires stream=True and n=1"
    INPUT_OWNERSHIP = "Model messages and registered Tools belong to the caller"
    INVALID_AUXILIARY = "Auxiliary parameters require an object"
    INVALID_PROCESSING = "Completion processor cannot change {key}"

    def accounting_request(self):
        from llm.core.model_protocol import ModelRequest, ToolMode
        from .wire_codec import decode_message, decode_tool_definition

        parameters = copy_model(self._value)
        messages = parameters.pop(self.MESSAGES)
        tools = parameters.pop(self.TOOLS, self.UNSPECIFIED)
        if not isinstance(messages, list) or (tools is not self.UNSPECIFIED and not isinstance(tools, list)):
            raise TypeError(self.INVALID_ACCOUNTING)
        choice = parameters.get(self.TOOL_CHOICE)
        mode = self.UNSPECIFIED
        if isinstance(choice, str) and choice in ToolMode:
            mode = ToolMode(parameters.pop(self.TOOL_CHOICE))
        return ModelRequest(
            [decode_message(message) for message in messages],
            parameters,
            tuple(decode_tool_definition(tool) for tool in ([] if tools is self.UNSPECIFIED else tools)),
            mode,
        )

    def accounting_payload(self, operation):
        return (
            self._value.get(self.INPUT)
            if operation == SDKOperation.ASYNC_EMBEDDING
            else [self._value.get(self.QUERY), self._value.get(self.DOCUMENTS)]
        )

    def set_accounting_content(self, text):
        from .response_values import SDKMessage

        self._value[self.MESSAGES] = [SDKMessage.accounting_wire(text)]

    @property
    def output_limit(self):
        return self._value.get(self.MAX_COMPLETION_TOKENS, self._value.get(self.MAX_TOKENS))

    def stream_request(self):
        if (
            self._value.get(self.STREAM, self.STREAMING) is not self.STREAMING
            or self._value.get(self.N, self.SINGLE_CHOICE) != self.SINGLE_CHOICE
        ):
            raise ValueError(self.INVALID_STREAM)
        return {**self._value, self.STREAM: self.STREAMING}

    def validate_input_ownership(self, input_fields, *, auxiliary=SDKRetryParameters.NOT_ENABLED):
        fields = (*input_fields, self.TOOL_CHOICE) if auxiliary else input_fields
        if not isinstance(self._value, dict) or any(key in self._value for key in fields):
            raise ValueError(self.INPUT_OWNERSHIP)

    def auxiliary_parameters(
        self,
        fields,
        sdk_retries,
        *,
        preserve_retries=SDKRetryParameters.NOT_ENABLED,
        preserve_output_limits=SDKRetryParameters.ENABLED,
    ):
        if not isinstance(self._value, dict):
            raise TypeError(self.INVALID_AUXILIARY)
        result = copy_model(self._value)
        for key in fields:
            if not preserve_retries or key not in self.RETRY_FIELDS:
                result.pop(key, self.UNSPECIFIED)
        if not preserve_retries:
            result.update({key: sdk_retries for key in self.RETRY_FIELDS})
        if not preserve_output_limits:
            for key in self.OUTPUT_LIMIT_FIELDS:
                result.pop(key, self.UNSPECIFIED)
        return result

    def validate_processing(self, original, fields):
        for key in fields:
            changed = (
                self._value.get(key) is not original.get(key)
                if key == self.CLIENT
                else self._value.get(key) != original.get(key)
            )
            if (key in self._value) != (key in original) or changed:
                raise ValueError(self.INVALID_PROCESSING.format(key=key))

    def count_tokens(self, sdk):
        return sdk.token_counter(
            model=self._value.get(self.MODEL),
            messages=self._value.get(self.MESSAGES),
            tools=self._value.get(self.TOOLS),
            tool_choice=self._value.get(self.TOOL_CHOICE),
        )

    @property
    def optional_api_base(self):
        return self._value.get(self.API_BASE)

    @property
    def api_base(self):
        return self._value[self.API_BASE]

    @property
    def optional_messages(self):
        return self._value.get(self.MESSAGES)

    @property
    def optional_tools(self):
        return self._value.get(self.TOOLS)

    @property
    def tools(self):
        return self._value[self.TOOLS]

    def validate_retry_contract(self, original):
        for key in self.RETRY_PROTECTED:
            if (key in self._value) != (key in original) or (
                key in original
                and (
                    self._value[key] is not original[key]
                    if key == self.CLIENT
                    else self._value[key] != original[key]
                )
            ):
                raise ValueError(self.INVALID_RETRY.format(key=key))

    @property
    def invalid_nonstreaming_single(self):
        return (
            self._value.get(self.STREAM, self.NONSTREAMING) is not self.NONSTREAMING
            or self._value.get(self.N, self.SINGLE_CHOICE) != self.SINGLE_CHOICE
        )

    def select_nonstreaming(self):
        self._value[self.STREAM] = self.NONSTREAMING

    def select_json_format(self):
        self._value[self.RESPONSE_FORMAT] = {self.FORMAT_TYPE: self.JSON_OBJECT}

    def clear_response_format(self):
        self._value.pop(self.RESPONSE_FORMAT, self.UNSPECIFIED)

    def embedding_request(self, inputs):
        return {**copy_model(self._value), self.INPUT: copy_model(inputs)}

    def rerank_request(self, query, documents):
        return {**copy_model(self._value), self.QUERY: query, self.DOCUMENTS: copy_model(documents)}


class EmbeddingParameters(ModelParameters):
    DIMENSIONS = "dimensions"

    @property
    def optional_dimensions(self):
        return self._value.get(self.DIMENSIONS)
