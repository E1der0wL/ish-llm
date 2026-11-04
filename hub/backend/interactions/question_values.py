"""The ask_user Tool definition, invocation arguments and answer boundary."""

from copy import deepcopy
from llm.core.interactions import InteractionOption, InteractionRequest
from llm.core.questions import QuestionMode
from llm.components.tools import QuestionHandler
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
        "Ask a necessary question that blocks further work. Execution pauses durably; "
        "the application saves the user's answer and explicitly resumes from the checkpoint. "
        "Choices are optional suggestions; free-text answers are always accepted."
    )
    NONBLOCKING_NAME = "ask_user_async"
    NONBLOCKING_DESCRIPTION = (
        "Post a durable question without pausing. Continue only work independent of the answer. "
        "The user chooses when and where to deliver their answer; never assume a suggested answer."
    )
    REVISION = "2"
    REPLY_ID = "reply"
    REPLY_LABEL = "Reply"
    CATEGORY = "hub.question"
    INPUT_SCHEMA = {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1}
    APPROVAL_REQUIRED = False
    OPERATION_KEY_REQUIRED = False

    @classmethod
    def create(cls, *, mode=QuestionMode.BLOCKING):
        from llm.components.tools import Tool

        return Tool(
            cls.NAME if mode == QuestionMode.BLOCKING else cls.NONBLOCKING_NAME,
            cls.DESCRIPTION if mode == QuestionMode.BLOCKING else cls.NONBLOCKING_DESCRIPTION,
            QuestionArguments.schema(),
            QuestionHandler(cls.request, mode=mode, revision=cls.REVISION),
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
    def tools(cls):
        return tuple(cls.create(mode=mode) for mode in QuestionMode)

    @classmethod
    def request(cls, values):
        arguments = QuestionArguments(values)
        return InteractionRequest(
            arguments.question,
            (InteractionOption(cls.REPLY_ID, cls.REPLY_LABEL),),
            kind=InteractionRequest.KIND_INPUT, category=cls.CATEGORY,
            description=arguments.description, input_schema=cls.input_schema(),
            source=cls.source(arguments),
        )

    @classmethod
    def input_schema(cls):
        return deepcopy(cls.INPUT_SCHEMA)

    @classmethod
    def source(cls, arguments, step_id=""):
        return QuestionSource.create(arguments.suggestions, step_id)
