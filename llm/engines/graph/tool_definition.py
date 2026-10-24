"""Tool node definition, schema and argument selection; Tool execution stays with its executor."""

from llm.components.workflows.bindings import WorkflowBindings
from llm.core.schema import object_schema, open_schema, field, SchemaType, OpenSchemaKind


class GraphToolDefinition:
    TOOL = "tool"
    ARGUMENTS = "arguments"
    ARGUMENTS_KEY = "arguments_key"
    RESULT_KEY = "result_key"
    PARAMETER_OWNER = "selected Tool parameter schema"
    NONEMPTY = 1
    SOURCE_LIMIT = 1
    NAME_FIELDS = (RESULT_KEY, ARGUMENTS_KEY)
    SOURCES = (WorkflowBindings.INPUTS, ARGUMENTS, ARGUMENTS_KEY)
    INVALID_NAME = "{key} must be a nonempty string"
    AMBIGUOUS_SOURCE = "Choose inputs, arguments or arguments_key"

    def __init__(self, value):
        self._value = value

    @property
    def tool(self):
        return self._value[self.TOOL]

    @property
    def has_arguments_key(self):
        return self.ARGUMENTS_KEY in self._value

    @property
    def arguments_key(self):
        return self._value[self.ARGUMENTS_KEY]

    @property
    def arguments(self):
        return self._value.get(self.ARGUMENTS, {})

    @classmethod
    def result(cls, definition, node_id, value):
        return {definition.get(cls.RESULT_KEY, node_id): value}

    @classmethod
    def schema(cls):
        return object_schema(
            {
                cls.TOOL: field(SchemaType.STRING, minLength=cls.NONEMPTY),
                cls.ARGUMENTS: open_schema(cls.PARAMETER_OWNER, category=OpenSchemaKind.IMPLEMENTATION),
                cls.ARGUMENTS_KEY: field(SchemaType.STRING, minLength=cls.NONEMPTY),
                cls.RESULT_KEY: field(SchemaType.STRING, minLength=cls.NONEMPTY),
            },
            required=[cls.TOOL],
        )

    @classmethod
    def validate_sources(cls, definition):
        for key in cls.NAME_FIELDS:
            if key in definition and (not isinstance(definition[key], str) or not definition[key]):
                raise ValueError(cls.INVALID_NAME.format(key=key))
        if sum(key in definition for key in cls.SOURCES) > cls.SOURCE_LIMIT:
            raise ValueError(cls.AMBIGUOUS_SOURCE)
