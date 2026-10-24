"""LiteLLM request fields and transport-copy operations; caller values stay opaque."""

from llm.core.model_protocol import copy_model


class SDKOperation:
    COMPLETION = "completion"
    ASYNC_COMPLETION = "acompletion"
    ASYNC_EMBEDDING = "aembedding"
    ASYNC_RERANK = "arerank"


class ModelParameters:
    MODEL = "model"

    def __init__(self, value):
        self._value = value

    @property
    def optional_model(self):
        return self._value.get(self.MODEL)

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
    SDK_MODULE = "litellm"

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

    @property
    def optional_api_base(self):
        return self._value.get(self.API_BASE)

    @property
    def api_base(self):
        return self._value[self.API_BASE]

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


class EmbeddingParameters(ModelParameters):
    DIMENSIONS = "dimensions"

    @property
    def optional_dimensions(self):
        return self._value.get(self.DIMENSIONS)
