"""Agent node selection, execution evidence and output projections; no execution policy."""

from copy import deepcopy
from dataclasses import dataclass, fields, replace
from typing import Any
import json

from llm.core.agents import AgentBinding, AgentDefinition
from llm.core.configuration import UNSET
from llm.core.json_data import JsonObject
from llm.core.results import OutputContract
from llm.core.schema import object_schema, field, open_schema, SchemaKeyword, SchemaType, OpenSchemaKind
from llm.services.infrastructure.storage import revision_token


class AgentNodeDefinition:
    """Interpret this action's fields while preserving the surrounding Graph definition."""

    class Field:
        AGENT = "agent"
        EMIT_TEXT = "emit_text"
        OUTPUT_FORMAT = AgentDefinition.Field.OUTPUT_FORMAT.value

    DEFAULT_EMIT_TEXT = False
    MINIMUM_AGENT_LENGTH = 1
    REQUIRED_AGENT = "Agent node requires an agent ID"
    WORKFLOW_OUTPUT = "Graph Agent nodes do not support output_format or emit_text"
    INVALID_FORMAT = "Agent output_format must be text or json"
    INVALID_EMIT = "Agent emit_text must be boolean"

    def __init__(self, value):
        self._value = value

    @classmethod
    def for_agent(cls, identifier):
        return {cls.Field.AGENT: identifier}

    @classmethod
    def describe_config(cls):
        return object_schema({
            cls.Field.AGENT: field(SchemaType.STRING, **{SchemaKeyword.MIN_LENGTH: cls.MINIMUM_AGENT_LENGTH}),
            cls.Field.EMIT_TEXT: field(SchemaType.BOOLEAN),
            cls.Field.OUTPUT_FORMAT: field(SchemaType.STRING, **{
                SchemaKeyword.ENUM: [kind.value for kind in AgentDefinition.OutputFormat]})},
            **{SchemaKeyword.REQUIRED: [cls.Field.AGENT]})

    @property
    def agent_id(self):
        identifier = self._value.get(self.Field.AGENT)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(self.REQUIRED_AGENT)
        return identifier

    def references(self):
        return (AgentDefinition.identifier(self._value[self.Field.AGENT]),)

    @property
    def emit_text(self):
        return self._value.get(self.Field.EMIT_TEXT, self.DEFAULT_EMIT_TEXT)

    def output_format(self, profile):
        return self._value.get(self.Field.OUTPUT_FORMAT, profile.output_format)

    def validate_workflow(self):
        if any(key in self._value for key in (self.Field.OUTPUT_FORMAT, self.Field.EMIT_TEXT)):
            raise ValueError(self.WORKFLOW_OUTPUT)

    def validate_behavior(self, profile):
        if self.output_format(profile) not in tuple(AgentDefinition.OutputFormat):
            raise ValueError(self.INVALID_FORMAT)
        if type(self.emit_text) is not bool:
            raise ValueError(self.INVALID_EMIT)


class AgentDelegationArguments:
    AGENT_ID = AgentBinding.Field.AGENT_ID.value
    INPUT = "input"
    INPUT_OWNER = "selected saved Agent input_schema"

    def __init__(self, value):
        self._value = value

    @classmethod
    def describe_config(cls):
        return object_schema({
            cls.AGENT_ID: field(SchemaType.STRING, **{SchemaKeyword.MIN_LENGTH: AgentNodeDefinition.MINIMUM_AGENT_LENGTH}),
            cls.INPUT: open_schema(cls.INPUT_OWNER, category=OpenSchemaKind.IMPLEMENTATION)},
            **{SchemaKeyword.REQUIRED: [cls.AGENT_ID, cls.INPUT]})

    @property
    def agent_id(self):
        return self._value[self.AGENT_ID]

    @property
    def input(self):
        return self._value[self.INPUT]

    @property
    def definition(self):
        return AgentNodeDefinition.for_agent(self.agent_id)


@dataclass(frozen=True, slots=True)
class AgentHandlerBinding:
    revision: str

    def to_dict(self):
        return {self.__class__.revision.__name__: self.revision}


class AgentExecutionBinding:
    """Detached open execution selection; public handler extensions remain intact."""

    class Field:
        AGENT_ID = AgentBinding.Field.AGENT_ID.value
        AGENT = "agent"
        AGENT_REVISION = "agent_revision"
        ENGINE = AgentDefinition.ENGINE_FIELD
        HANDLER_REVISION = "handler_revision"
        CONFIGURATION = "configuration"
        WORKFLOW_ID = "workflow_id"
        RESOURCES = "resources"
        EXECUTION = "execution"

    STATE_FIELD = "agent"

    def __init__(self, value):
        # The public binding hook may add fields. Keep them in identity evidence,
        # rather than normalizing an open extension into a second schema.
        self._value = deepcopy(value)

    @classmethod
    def create(cls, identifier, profile, handler_revision, configuration, *,
               workflow=UNSET, resources=UNSET, execution=UNSET):
        value = {
            cls.Field.AGENT_ID: identifier, cls.Field.AGENT: profile.to_dict(),
            cls.Field.AGENT_REVISION: profile.revision, cls.Field.ENGINE: profile.engine,
            cls.Field.HANDLER_REVISION: handler_revision, cls.Field.CONFIGURATION: configuration,
        }
        if workflow is not UNSET:
            value[cls.Field.WORKFLOW_ID] = workflow
        if resources is not UNSET:
            value[cls.Field.RESOURCES] = resources.to_dict()
        if execution is not UNSET:
            value[cls.Field.EXECUTION] = execution
        JsonObject.validate(value)
        return cls(value)

    @property
    def agent_id(self):
        return self._value[self.Field.AGENT_ID]

    @property
    def agent_revision(self):
        return self._value[self.Field.AGENT_REVISION]

    @property
    def engine(self):
        return self._value[self.Field.ENGINE]

    def to_dict(self):
        return deepcopy(self._value)

    def state(self):
        return {self.STATE_FIELD: self.to_dict()}

    @classmethod
    def references(cls, value):
        return {value[cls.Field.AGENT_ID]} if cls.Field.AGENT_ID in value else set()


class AgentStepMetadata:
    """Observations inherited by child Steps; explicit null and extensions are retained."""

    INPUTS = "inputs"
    NODE_ID = "node_id"
    AGENT_STEP_ID = "agent_step_id"
    DELEGATION_STEP_ID = "delegation_step_id"
    PARENT_STEP_ID = "parent_step_id"
    TOOL_CALLS = "tool_calls"
    COMPLETED_TOOLS = "completed_tools"

    @staticmethod
    def selected_id(metadata):
        return metadata.get(AgentExecutionBinding.Field.AGENT_ID)

    @classmethod
    def started(cls, binding, inputs):
        return {**binding.to_dict(), cls.INPUTS: inputs}

    @classmethod
    def graph_child(cls, metadata, step_id):
        return {**metadata, cls.AGENT_STEP_ID: metadata.get(cls.AGENT_STEP_ID, step_id)}

    @classmethod
    def delegated(cls, metadata, step_id):
        return {**metadata, cls.DELEGATION_STEP_ID: step_id,
                cls.PARENT_STEP_ID: metadata.get(cls.PARENT_STEP_ID, step_id)}

    @classmethod
    def completed_usage(cls, calls, completed):
        return {cls.TOOL_CALLS: calls, cls.COMPLETED_TOOLS: completed}

    @classmethod
    def behavior_child(cls, metadata, binding, node_id, step_id):
        selection = AgentExecutionBinding.Field
        return {**metadata,
                selection.AGENT_ID: metadata.get(selection.AGENT_ID, binding.agent_id),
                selection.AGENT_REVISION: metadata.get(selection.AGENT_REVISION, binding.agent_revision),
                selection.ENGINE: metadata.get(selection.ENGINE, binding.engine),
                cls.NODE_ID: metadata.get(cls.NODE_ID, node_id),
                cls.AGENT_STEP_ID: metadata.get(cls.AGENT_STEP_ID, step_id)}


@dataclass(frozen=True, slots=True)
class AgentInvocationIdentity:
    agent_id: str
    revision: str
    engine: str
    mode: str
    configuration: str | None
    resources: dict | None

    def to_dict(self):
        return {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)}


@dataclass(frozen=True, slots=True)
class AgentExecutionRevision:
    """Hash the existing opaque Engine binding without changing JSON encoder semantics."""

    value: Any

    @property
    def token(self):
        return revision_token({self.__class__.value.__name__: self.value})


@dataclass(frozen=True, slots=True)
class AgentInvocationBinding:
    NO_EXECUTION = None

    identity: AgentInvocationIdentity
    tools: dict
    execution: str | None

    def to_dict(self):
        # ToolRegistry owns its public projection, including application overrides.
        # Merge order is part of the existing execution identity.
        value = {**self.identity.to_dict(), **deepcopy(self.tools),
                 self.__class__.execution.__name__: self.execution}
        JsonObject.validate(value)
        return value


@dataclass(frozen=True, slots=True)
class AgentOutputValue:
    """Agent output Schema sees absent data differently from parsed JSON null."""

    text: str
    data: Any = UNSET

    @classmethod
    def workflow(cls, value):
        return cls(OutputContract.EMPTY_TEXT, value)

    @classmethod
    def from_output(cls, output, output_format):
        data = output.data
        if output.has_data:
            return cls(output.text, data)
        if output_format == AgentDefinition.OutputFormat.JSON:
            return cls(output.text, json.loads(output.text))
        return cls(output.text)

    @property
    def engine_data(self):
        return OutputContract.NO_DATA if self.data is UNSET else self.data

    def to_dict(self):
        value = {self.__class__.text.__name__: self.text}
        if self.data is not UNSET:
            value[self.__class__.data.__name__] = self.data
        return value

    def apply_to(self, output):
        return replace(output, data=self.data) if not output.has_data and self.data is not UNSET else output
