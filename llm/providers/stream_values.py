"""Synchronous stream bridge protocol and Provider-only observations."""

from uuid import uuid4

from .diagnostic_values import ProviderAttempt
from .request_values import SDKOperation, SDKRetryParameters, LiteLLMParameters


class StreamBridge:
    BUFFER_SIZE = 8
    MINIMUM_BUFFER_SIZE = 1
    DELIVERY_POLL_SECONDS = 0.05
    THREAD_NAME = "ish-litellm-stream"
    DAEMON = True
    CHUNK = "chunk"
    ERROR = "error"
    END = "end"
    REQUEST = "request"
    OPEN = "stream_open"
    READ = "stream_read"
    CLOSE = "stream_close"
    CLOSE_METHOD = "close"
    ASYNC_CLOSE_METHOD = "aclose"
    INITIAL_CHUNKS = 0
    CHUNK_INCREMENT = 1
    UNSPECIFIED = None
    ACTIVE = True
    INACTIVE = False
    INVALID_BUFFER = "buffer_size must be a positive integer"

    @classmethod
    def closing_method(cls, stream):
        return getattr(stream, cls.CLOSE_METHOD, cls.UNSPECIFIED) or getattr(
            stream, cls.ASYNC_CLOSE_METHOD, cls.UNSPECIFIED
        )

    @classmethod
    def observe_attempt(cls, details):
        details.setdefault(ProviderAttempt.call_id.__name__, uuid4().hex)
        details.setdefault(ProviderAttempt.attempt.__name__, ProviderAttempt.ORDINAL_INCREMENT)


class ProviderSDKObservation:
    REQUEST = "provider_request"
    NO_CACHE = "none"
    CONFIGURED_CACHE = "configured"
    NO_TOOLS = 0
    UNSPECIFIED = None

    @classmethod
    def request(cls, operation, request, sdk):
        from .runtime import diagnostic

        diagnostic(
            cls.REQUEST,
            operation=operation,
            num_retries=request.get(SDKRetryParameters.NUM_RETRIES),
            max_retries=request.get(SDKRetryParameters.MAX_RETRIES),
            caching=request.get(LiteLLMParameters.CACHING),
            cache=request.get(LiteLLMParameters.CACHE),
            sdk_cache=cls.NO_CACHE if sdk.cache is cls.UNSPECIFIED else cls.CONFIGURED_CACHE,
        )

    @classmethod
    def tool_count(cls, request):
        definitions = LiteLLMParameters(request).optional_tools
        return len(definitions) if isinstance(definitions, (list, tuple)) else cls.NO_TOOLS


class ProviderTokenAccounting:
    AUXILIARY = (SDKOperation.ASYNC_EMBEDDING, SDKOperation.ASYNC_RERANK)
    COMPLETION = (SDKOperation.COMPLETION, SDKOperation.ASYNC_COMPLETION)
    NO_OUTPUT = 0
    MINIMUM_OUTPUT = 1
    MINIMUM_INPUT = 0
    ENSURE_ASCII = False
    OUTPUT_REQUIRED = "Token quota requires an explicit completion output limit"
    UNSUPPORTED_OPERATION = "Adapter has no token accounting contract for this operation"
    INVALID_COUNT = "Token counter must return a nonnegative integer"
