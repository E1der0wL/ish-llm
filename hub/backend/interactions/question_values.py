"""The ask_user Tool definition, invocation arguments and answer boundary."""

from copy import deepcopy
from dataclasses import dataclass
from llm.core.interactions import InteractionRequest
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean
from llm.core.tool_contracts import ToolContract, ToolEffect, ToolIsolation
from ...model import QuestionSource


class QuestionArguments:
    QUESTION = "question"
    DESCRIPTION = "description"
    SUGGESTIONS = QuestionSource.SUGGESTIONS.value
    SCHEMA = {
        SchemaKeyword.TYPE: SchemaType.OBJECT,
        SchemaKeyword.PROPERTIES: {
            QUESTION: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.MIN_LENGTH: 1,
            },
            DESCRIPTION: {SchemaKeyword.TYPE: SchemaType.STRING},
            SUGGESTIONS: {
                SchemaKeyword.TYPE: SchemaType.ARRAY,
                SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING},
            },
        },
        SchemaKeyword.REQUIRED: [QUESTION],
        SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
    }

    def __init__(self, value):
        self._value = value

    @property
    def question(self):
        return self._value[self.QUESTION]

    @property
    def description(self):
        return self._value.get(self.DESCRIPTION, InteractionRequest.EMPTY_DESCRIPTION)

    @property
    def suggestions(self):
        return self._value.get(self.SUGGESTIONS, [])

    @classmethod
    def schema(cls):
        return deepcopy(cls.SCHEMA)


class QuestionTool:
    NAME = "ask_user"
    DESCRIPTION = (
        "Ask the user a necessary question and wait for their answer, then continue the SAME task. "
        "Use this instead of ending a response with a question when work needs user input. "
        "Choices are optional suggestions; free-text answers are always accepted."
    )
    REVISION = "1"
    REPLY_ID = "reply"
    REPLY_LABEL = "Reply"
    CATEGORY = "hub.question"
    INPUT_SCHEMA = {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1}
    APPROVAL_REQUIRED = False
    OPERATION_KEY_REQUIRED = False

    @classmethod
    def create(cls, handler):
        from llm.components.tools import Tool

        return Tool(
            cls.NAME,
            cls.DESCRIPTION,
            QuestionArguments.schema(),
            handler,
            contract=ToolContract(
                revision=cls.REVISION,
                effect=ToolEffect.READ_ONLY,
                isolation=ToolIsolation.NONE,
                approval_required=cls.APPROVAL_REQUIRED,
                operation_key_required=cls.OPERATION_KEY_REQUIRED,
                retry_fields=(),
            ),
        )

    @classmethod
    def input_schema(cls):
        return deepcopy(cls.INPUT_SCHEMA)

    @classmethod
    def source(cls, arguments):
        return QuestionSource.create(arguments.suggestions)


@dataclass(frozen=True, slots=True)
class QuestionReply:
    answer: object

    def to_record(self):
        return {type(self).answer.__name__: self.answer}
