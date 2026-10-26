"""Graph-owned explicit execution parameters and admitted Agent selection."""

from copy import deepcopy
from llm.components.workflows.contracts import WorkflowNode
from llm.core.agents import AgentDefinition
from llm.core.configuration_views import EngineConfigurationKey
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.parameters import ParameterLayout
from llm.core.schema import (
    field,
    object_schema,
    validate_declared_fields,
    SchemaKeyword,
    SchemaType,
    SchemaExtension,
    SchemaNarrowing,
)


class GraphRegistration:
    DEFAULT_REVISION = "1"
    ABSENT = None
    INVALID_PARAMETER_KEY = "parameter_key must be nonempty text"
    INVALID_CONFIG_KEYS = "config_keys must be a tuple of project setting names"
    INVALID_REVISION = "GraphEngine revision must be nonempty text"
    INVALID_HANDLER = "Invalid handler or reserved control node type"
    HANDLERS = "handlers"
    REVISION = "revision"
    CONFIG_KEYS = "config_keys"
    RUNTIME_FIELDS = (HANDLERS, REVISION, CONFIG_KEYS)

    @classmethod
    def handler_registry(cls, engine):
        return getattr(engine, cls.HANDLERS, {})

    @classmethod
    def validate_parameter_key(cls, value):
        if value is not cls.ABSENT and (not isinstance(value, str) or not value.strip()):
            raise ValueError(cls.INVALID_PARAMETER_KEY)

    @classmethod
    def validate_config_keys(cls, value):
        if value is not cls.ABSENT and (
            not isinstance(value, tuple) or any(not isinstance(k, str) or not k for k in value)
        ):
            raise ValueError(cls.INVALID_CONFIG_KEYS)

    @classmethod
    def validate_revision(cls, value):
        if not isinstance(value, str) or not value:
            raise ValueError(cls.INVALID_REVISION)

    @classmethod
    def validate_handlers(cls, handlers, controls):
        if any(
            not isinstance(name, str) or not name or name in controls or not callable(handler)
            for name, handler in handlers.items()
        ):
            raise ValueError(cls.INVALID_HANDLER)


class GraphParameters:
    MAX_STEPS = "max_steps"
    MAX_PARALLELISM = "max_parallelism"
    TIMEOUT_SECONDS = WorkflowNode.TIMEOUT_SECONDS
    BUFFER_SIZE = "buffer_size"
    MAX_NESTED_DEPTH = "max_nested_depth"
    CLEANUP_TIMEOUT = "cleanup_timeout"
    DEFAULT_BUFFER_SIZE = 32
    POSITIVE_MINIMUM = 0
    MINIMUM_COUNT = 1
    MINIMUM_DEPTH = 0
    UNCONFIGURED = False
    CONFIGURED = True
    NAMES = (MAX_STEPS, MAX_PARALLELISM, TIMEOUT_SECONDS, BUFFER_SIZE, MAX_NESTED_DEPTH, CLEANUP_TIMEOUT)
    LAYOUT = ParameterLayout(
        config=(BUFFER_SIZE, CLEANUP_TIMEOUT),
        policy=(MAX_STEPS, MAX_PARALLELISM, TIMEOUT_SECONDS, MAX_NESTED_DEPTH),
    )

    @classmethod
    def schema(cls, option_names, timeout_parameter):
        properties = {
            key: field([SchemaType.INTEGER, SchemaType.NULL], minimum=cls.MINIMUM_COUNT)
            for key in option_names
        }
        properties[cls.BUFFER_SIZE][SchemaKeyword.TYPE] = SchemaType.INTEGER
        properties[cls.MAX_NESTED_DEPTH].update({SchemaKeyword.MINIMUM: cls.MINIMUM_DEPTH})
        for key in (timeout_parameter, cls.CLEANUP_TIMEOUT):
            properties[key] = field(
                [SchemaType.NUMBER, SchemaType.NULL] if key == timeout_parameter else SchemaType.NUMBER,
                exclusiveMinimum=cls.POSITIVE_MINIMUM,
            )
        for key in (cls.MAX_STEPS, cls.MAX_PARALLELISM, timeout_parameter, cls.MAX_NESTED_DEPTH):
            properties[key][SchemaExtension.NARROWING] = SchemaNarrowing.MAXIMUM
        return properties

    @classmethod
    def configure(cls, engine, values):
        validate_declared_fields(
            {name: values[name] for name in engine._option_names if name in values}, engine._option_schema()
        )
        for key in engine._option_names:
            setattr(engine, key, values.get(key))
        engine.buffer_size = values.get(cls.BUFFER_SIZE, engine.DEFAULT_BUFFER_SIZE)

    @staticmethod
    def describe(engine):
        properties = engine._option_schema()
        return engine.parameter_layout.schema(
            object_schema(
                properties,
                **{
                    SchemaExtension.RUNTIME_CONFIGURATION: list(GraphRegistration.RUNTIME_FIELDS),
                    **EngineConfigurationKey.schema_fields(engine.parameter_key),
                },
            )
        )


class GraphAgentOptions:
    WORKFLOW = WorkflowNode.WORKFLOW
    ALLOWED = {WORKFLOW, *ImplementationParameters.SECTIONS}
    ABSENT = None
    INVALID = "Unsupported Graph Agent engine_options"
    INVALID_WORKFLOW = "Graph Agent workflow must be a nonempty ID"
    WORKFLOW_PATTERN = r"\S"

    def __init__(self, value):
        self._value = value

    @classmethod
    def describe(cls, configuration):
        schema = deepcopy(configuration)
        schema[SchemaKeyword.PROPERTIES][cls.WORKFLOW] = field(
            SchemaType.STRING, minLength=AgentDefinition.MINIMUM_TEXT_LENGTH, pattern=cls.WORKFLOW_PATTERN)
        schema[SchemaKeyword.REQUIRED] = [*schema.get(SchemaKeyword.REQUIRED, ()), cls.WORKFLOW]
        return schema

    @classmethod
    def from_definition(cls, definition):
        return cls(deepcopy(definition.get(AgentDefinition.Field.ENGINE_OPTIONS.value, {})))

    @classmethod
    def discover(cls, definition):
        return definition.get(AgentDefinition.Field.ENGINE_OPTIONS.value, {}).get(cls.WORKFLOW)

    def validate_fields(self):
        if self._value.keys() - self.ALLOWED:
            raise ValueError(self.INVALID)

    def pop_workflow(self):
        return self._value.pop(self.WORKFLOW, self.ABSENT)

    @classmethod
    def validate_workflow(cls, workflow):
        if not isinstance(workflow, str) or not workflow.strip():
            raise ValueError(cls.INVALID_WORKFLOW)

    def to_dict(self):
        return self._value
