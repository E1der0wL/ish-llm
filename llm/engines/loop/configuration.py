"""Loop-owned parameter layout, schema and presence-preserving Agent overrides."""

from copy import deepcopy
from llm.core.agents import AgentDefinition
from llm.core.configuration_views import EngineConfigurationKey
from llm.core.model_protocol import copy_model
from llm.core.parameters import ParameterLayout
from llm.core.schema import object_schema, field, SchemaKeyword, SchemaType, SchemaExtension, SchemaNarrowing
from llm.policies import CompletionPolicy
from llm.components.review import ReviewPolicy
from llm.providers.requests import provider_schema


class LoopRegistration:
    ABSENT = None
    INVALID_PARAMETER_KEY = "parameter_key must be a nonempty string or None"
    INVALID_RETRY_SELECTOR = "Retry selector must be callable"
    INVALID_RECOVERY_SELECTOR = "Completion recovery selector must be callable"

    @classmethod
    def validate_key(cls, value):
        if value is not cls.ABSENT and (not isinstance(value, str) or not value.strip()):
            raise ValueError(cls.INVALID_PARAMETER_KEY)

    @classmethod
    def validate_retry(cls, value):
        if value is not cls.ABSENT and not callable(value):
            raise TypeError(cls.INVALID_RETRY_SELECTOR)

    @classmethod
    def validate_recovery(cls, value):
        if value is not cls.ABSENT and not callable(value):
            raise TypeError(cls.INVALID_RECOVERY_SELECTOR)


class LoopParameters:
    COMPLETION = "completion"
    SYSTEM_PROMPT = "system_prompt"
    BUFFER_SIZE = "buffer_size"
    MAX_ITERATIONS = "max_iterations"
    REQUEST_TIMEOUT = "request_timeout"
    TOOL_TIMEOUT = "tool_timeout"
    MAX_TOOL_CALLS = "max_tool_calls"
    MAX_ARGUMENT_CHARS = "max_argument_chars"
    MAX_OUTPUT_CHARS = "max_output_chars"
    INPUT_POLICY = "input_policy"
    PROVIDER = "provider"
    FINAL_REVIEW = "final_review"
    INPUT_POLICY_PATH = "policy.completion"
    TIMEOUT_SUFFIX = "timeout"
    POSITIVE_MINIMUM = 0
    PROMPT_DESCRIPTION = "시스템 프롬프트"
    LAYOUT = ParameterLayout(
        config=(COMPLETION, SYSTEM_PROMPT, BUFFER_SIZE),
        policy=(
            MAX_ITERATIONS,
            REQUEST_TIMEOUT,
            TOOL_TIMEOUT,
            MAX_TOOL_CALLS,
            MAX_ARGUMENT_CHARS,
            MAX_OUTPUT_CHARS,
            PROVIDER,
            FINAL_REVIEW,
        ),
        paths={INPUT_POLICY: INPUT_POLICY_PATH},
    )
    NAMES = (
        MAX_ITERATIONS,
        REQUEST_TIMEOUT,
        TOOL_TIMEOUT,
        BUFFER_SIZE,
        MAX_TOOL_CALLS,
        MAX_ARGUMENT_CHARS,
        MAX_OUTPUT_CHARS,
    )

    def __init__(self, value):
        self._value = value

    def declared(self, names):
        return {name: self._value[name] for name in names if name in self._value}

    def execution_values(self, names):
        return (self._value.get(key) for key in names if key != self.BUFFER_SIZE)

    def buffer_size(self, default):
        return self._value.get(self.BUFFER_SIZE, default)

    @property
    def input_policy(self):
        return self._value.get(self.INPUT_POLICY)

    @property
    def provider(self):
        return self._value.get(self.PROVIDER)

    @property
    def final_review(self):
        return self._value.get(self.FINAL_REVIEW)

    @property
    def completion(self):
        return self._value.get(self.COMPLETION, {})

    @property
    def system_prompt(self):
        return self._value.get(self.SYSTEM_PROMPT)

    @classmethod
    def option_schema(cls, names):
        return {
            name: field(
                (
                    [SchemaType.NUMBER, SchemaType.NULL]
                    if name.endswith(cls.TIMEOUT_SUFFIX)
                    else (
                        SchemaType.INTEGER
                        if name == cls.BUFFER_SIZE
                        else [SchemaType.INTEGER, SchemaType.NULL]
                    )
                ),
                exclusiveMinimum=cls.POSITIVE_MINIMUM,
            )
            for name in names
        }

    @classmethod
    def describe(cls, engine):
        properties = engine._option_schema()
        properties[cls.SYSTEM_PROMPT] = {
            SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL],
            SchemaKeyword.DESCRIPTION: cls.PROMPT_DESCRIPTION,
        }
        properties[cls.COMPLETION] = engine.completion_adapter.describe_config()
        properties[cls.INPUT_POLICY] = CompletionPolicy.describe_config()
        properties[cls.PROVIDER] = {
            **provider_schema(),
            SchemaKeyword.TYPE: [SchemaType.OBJECT, SchemaType.NULL],
        }
        properties[cls.FINAL_REVIEW] = ReviewPolicy.schema()
        for key in engine._option_names:
            if key != cls.BUFFER_SIZE:
                properties[key][SchemaExtension.NARROWING] = SchemaNarrowing.MAXIMUM
        return engine.parameter_layout.schema(
            object_schema(properties, **EngineConfigurationKey.schema_fields(engine.parameter_key))
        )


class LoopPolicy:
    """Projection of the Loop-owned explicit policy section; no defaults are stored."""

    def __init__(self, value):
        self._value = value

    @property
    def completion(self):
        return self._value.get(CompletionPolicy.NAME)

    @property
    def provider(self):
        return self._value.get(LoopParameters.PROVIDER)

    @property
    def final_review(self):
        return self._value.get(LoopParameters.FINAL_REVIEW)


class LoopAgentOptions:
    """Only the selected Loop interprets its Agent overrides; source fields stay omitted."""

    ENGINE_OPTIONS = AgentDefinition.Field.ENGINE_OPTIONS.value
    COMPLETION = AgentDefinition.Field.COMPLETION.value
    SYSTEM_PROMPT = AgentDefinition.Field.SYSTEM_PROMPT.value
    POLICY = AgentDefinition.Field.POLICY.value
    REQUIRE_TOOL = AgentDefinition.PolicyField.REQUIRE_TOOL.value
    RUNTIME_REQUIRED_TOOL = "_require_tool"
    NO_REQUIRED_TOOL = False
    PROMPT_SEPARATOR = "\n\n"
    INVALID_INSTRUCTIONS = "Agent instructions must be text"

    def __init__(self, value):
        self._value = value

    @property
    def completion(self):
        return self._value.get(self.COMPLETION, {})

    @property
    def engine_options(self):
        return self._value.get(self.ENGINE_OPTIONS, {})

    @property
    def has_prompt(self):
        return self.SYSTEM_PROMPT in self._value

    @property
    def prompt(self):
        return self._value[self.SYSTEM_PROMPT]

    @property
    def required_completion(self):
        return self._value[self.COMPLETION]

    @property
    def optional_completion(self):
        return self._value.get(self.COMPLETION)

    @property
    def require_tool(self):
        return self._value.get(self.POLICY, {}).get(self.REQUIRE_TOOL, self.NO_REQUIRED_TOOL)

    @classmethod
    def prompt_value(cls, prompt):
        return {cls.SYSTEM_PROMPT: prompt}

    @classmethod
    def create(cls, options, completion, prompt):
        return {cls.ENGINE_OPTIONS: options, cls.COMPLETION: completion, **prompt}

    @classmethod
    def with_prompt(cls, options, prompt):
        return {**options, cls.SYSTEM_PROMPT: prompt}

    @classmethod
    def set_prompt(cls, overrides, prompt):
        overrides[cls.SYSTEM_PROMPT] = prompt

    @classmethod
    def set_completion(cls, overrides, completion):
        overrides[cls.COMPLETION] = completion

    @classmethod
    def join_prompt(cls, parts):
        return cls.PROMPT_SEPARATOR.join(parts)

    @classmethod
    def required_by(cls, engine):
        return getattr(engine, cls.RUNTIME_REQUIRED_TOOL, cls.NO_REQUIRED_TOOL)
