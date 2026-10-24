"""LiteLLM response projections; optional wire fields are not persisted evidence."""

from .embeddings import value


class SDKMessage:
    ROLE = "role"
    CONTENT = "content"
    TOOL_CALLS = "tool_calls"
    TOOL_CALL_ID = "tool_call_id"
    NAME = "name"
    FIELDS = frozenset((ROLE, CONTENT, TOOL_CALLS, TOOL_CALL_ID, NAME))

    def __init__(self, message):
        self._value = message

    @property
    def content(self):
        return value(self._value, self.CONTENT)


class SDKCompletionResponse:
    CHOICES = "choices"
    MESSAGE = "message"

    def __init__(self, response):
        self._value = response

    @property
    def choices(self):
        return value(self._value, self.CHOICES)

    @classmethod
    def message(cls, choice):
        return SDKMessage(value(choice, cls.MESSAGE))
