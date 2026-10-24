"""Placement of transient Hub model context after the leading system message."""

from llm.components.processing import CompletionMessage
from llm.core.model_protocol import ModelMessage, ModelRole


class HubInputContext:
    FIRST = 0
    AFTER_SYSTEM = 1
    SEPARATOR = "\n\n"

    @classmethod
    def insert(cls, request, text):
        index = (
            cls.AFTER_SYSTEM
            if request.messages
            and request.messages[cls.FIRST].message.role == ModelRole.SYSTEM
            else cls.FIRST
        )
        request.messages.insert(
            index, CompletionMessage(ModelMessage(ModelRole.SYSTEM, text))
        )
