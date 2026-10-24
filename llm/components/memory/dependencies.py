"""Memory's runtime injection checks shared by Component and Processor."""

from llm.providers.streaming import CompletionAdapter


class MemoryDependencies:
    ABSENT = None
    INVALID_CALLABLE = "Memory model and token counter must be callable"
    INVALID_ADAPTER = "Memory completion_adapter requires stream"

    @classmethod
    def validate_callables(cls, *values):
        if any(value is not cls.ABSENT and not callable(value) for value in values):
            raise TypeError(cls.INVALID_CALLABLE)

    @classmethod
    def completion(cls, adapter):
        from llm.providers.litellm import LiteLLMCompletionAdapter
        selected = LiteLLMCompletionAdapter() if adapter is cls.ABSENT else adapter
        if not callable(getattr(selected, CompletionAdapter.stream.__name__, cls.ABSENT)):
            raise TypeError(cls.INVALID_ADAPTER)
        return selected
