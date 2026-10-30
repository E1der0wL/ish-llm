"""Host-selected OpenAI-compatible transport, without changing stored evidence."""

from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.request_values import LiteLLMParameters
from llm.providers.runtime import ProviderRequestDiagnostics
from llm.providers.wire_codec import SDKMessageCodec, SDKWire


class CompatibleMessages:
    """Only explicit OpenAI-compatible endpoints need this wire accommodation."""

    PROVIDER = "custom_llm_provider"
    PROVIDERS = frozenset(("openai", "hosted_vllm"))
    OFFICIAL_HOST = "api.openai.com"
    SCHEMES = frozenset(("http", "https"))
    EMPTY_TEXT = ""
    UNSPECIFIED = None
    SEPARATOR = "/"
    DIAGNOSTIC = "hub_message_shapes"
    LIMIT = 256
    INDEX = "index"
    ROLE = "role"
    CONTENT = "content_type"
    PRESENT = "content_present"
    NORMALIZED = "empty_tool_call_content"
    CALLS = "tool_call_count"
    TEXT = "text"
    NULL = "null_or_omitted"
    PARTS = "parts"

    @classmethod
    def selected(cls, request: ModelRequest) -> bool:
        parameters = request.parameters
        endpoint = parameters.get(LiteLLMParameters.API_BASE)
        if not isinstance(endpoint, str):
            return False
        try:
            url = urlsplit(endpoint)
            if url.scheme not in cls.SCHEMES or not url.hostname or url.hostname == cls.OFFICIAL_HOST:
                return False
        except ValueError:
            return False
        provider = parameters.get(cls.PROVIDER)
        if provider is cls.UNSPECIFIED:
            model = parameters.get(LiteLLMParameters.MODEL)
            provider = model.partition(cls.SEPARATOR)[0] if isinstance(model, str) else cls.EMPTY_TEXT
        return isinstance(provider, str) and provider in cls.PROVIDERS

    @classmethod
    def empty_call(cls, message: ModelMessage) -> bool:
        return message.role == ModelRole.ASSISTANT and bool(message.calls) and message.content is cls.UNSPECIFIED

    @classmethod
    def transport(cls, request: ModelRequest) -> ModelRequest:
        if not cls.selected(request) or not any(cls.empty_call(message) for message in request.messages):
            return request
        return replace(request, messages=[
            replace(message, content=cls.EMPTY_TEXT) if cls.empty_call(message) else message
            for message in request.messages
        ])

    @classmethod
    def describe(cls, request: ModelRequest) -> list[dict[str, Any]]:
        selected = cls.selected(request)
        rows = []
        for index, message in enumerate(request.messages[:cls.LIMIT]):
            content = message.content if message.result is cls.UNSPECIFIED else message.result.content
            shape = message.opaque.get(SDKWire.SHAPE, {})
            missing = (shape.get(SDKMessageCodec.CONTENT_MISSING, False)
                       and message.content is cls.UNSPECIFIED and message.result is cls.UNSPECIFIED)
            rows.append({cls.INDEX: index, cls.ROLE: message.role.value,
                         cls.PRESENT: not missing,
                         cls.CONTENT: cls.NULL if content is cls.UNSPECIFIED else
                         cls.TEXT if isinstance(content, str) else cls.PARTS,
                         cls.NORMALIZED: selected and cls.empty_call(message), cls.CALLS: len(message.calls)})
        return rows


class HubCompletionAdapter(LiteLLMCompletionAdapter):
    """Use the native decoder, validation, retry policy and streaming lifecycle."""

    def describe_request(self, request):
        details = super().describe_request(request)
        # Shapes contain no body, arguments, Tool IDs, endpoint or credentials.
        if ProviderRequestDiagnostics.REQUEST_SUMMARY in details:
            details[CompatibleMessages.DIAGNOSTIC] = CompatibleMessages.describe(request)
        return details

    def describe_call(self, request):
        return super().describe_call(CompatibleMessages.transport(request))

    def stream(self, request, *, buffer_size):
        request.validate_exchanges()
        return super().stream(CompatibleMessages.transport(request), buffer_size=buffer_size)
