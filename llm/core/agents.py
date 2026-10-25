"""Independent Agent definition and current storage envelopes; no execution state."""

from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from types import MappingProxyType
import hashlib
import json
import re
from enum import StrEnum

from jsonschema import Draft202012Validator
from llm.core.json_data import JsonObject
from llm.core.schema import (open_schema, metadata_schema, validate_local_refs, SchemaKeyword,
                             SchemaType, SchemaBoolean, OpenSchemaKind)
from llm.core.configuration import UNSET


class AgentMode(StrEnum):
    BEHAVIORAL = "behavioral"
    WORKFLOW = "workflow"


class AgentDefinition:
    """A selected Engine is required; application descriptions remain optional."""

    NO_PURPOSE = ""

    REFERENCE_COMPONENT = "agents"
    CAPABILITY = REFERENCE_COMPONENT
    class Field(StrEnum):
        PURPOSE = "purpose"
        DESCRIPTION = "description"
        METADATA = "metadata"
        ENGINE = "engine"
        SYSTEM_PROMPT = "system_prompt"
        COMPLETION = "completion"
        ENGINE_OPTIONS = "engine_options"
        INPUT_SCHEMA = "input_schema"
        OUTPUT_SCHEMA = "output_schema"
        TOOLS = "tools"
        RESOURCES = "resources"
        POLICY = "policy"
        OUTPUT_FORMAT = "output_format"

    class ResourceField(StrEnum):
        PROMPT = "prompt"
        SKILLS = "skills"
        RAG = "rag"
        MCP = "mcp"

    class PolicyField(StrEnum):
        TIMEOUT_SECONDS = "timeout_seconds"
        MAX_TOOL_CALLS = "max_tool_calls"
        REQUIRE_TOOL = "require_tool"

    class OutputFormat(StrEnum):
        TEXT = "text"
        JSON = "json"

    ENGINE_FIELD = Field.ENGINE.value
    WORKFLOW_FIELDS = frozenset((Field.PURPOSE, Field.DESCRIPTION, Field.ENGINE,
                                Field.ENGINE_OPTIONS, Field.METADATA))
    JSON_SCHEMA_FIELDS = (Field.INPUT_SCHEMA, Field.OUTPUT_SCHEMA)
    IDENTIFIER_PATTERN = r"[A-Za-z0-9_-]{1,64}"
    RESOURCE_NAME_PATTERN = "^[A-Za-z0-9_-]{1,64}$"
    MINIMUM_TEXT_LENGTH = 1
    MINIMUM_RESOURCE_PROPERTIES = 1
    MINIMUM_TOOL_CALLS = 1
    MINIMUM_TIMEOUT_EXCLUSIVE = 0
    COMPLETION_OWNER = "selected Agent Engine completion adapter"
    OPTIONS_OWNER = "selected Agent Engine.for_agent"
    INPUT_SCHEMA_OWNER = "Agent input JSON Schema"
    OUTPUT_SCHEMA_OWNER = "Agent output JSON Schema"
    UNSUPPORTED_WORKFLOW = "Graph Agent only orchestrates Workflows; unsupported fields: "
    FIELD_SEPARATOR = ", "
    INVALID_ID = "Invalid Agent ID"
    ERROR_PATH_SEPARATOR = "."
    RECORD_LOCATION = "record"
    INVALID_DEFINITION = "agents/{identifier}: {location}: {message}"
    REQUIRED_TOOL = "require_tool needs at least one allowed Tool"
    REVISION_SORT_KEYS = True
    REVISION_ENSURE_ASCII = False
    REVISION_ENCODING = "utf-8"
    SELECTED_VALIDATION_ID = "selected"

    schema = {
        SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.REQUIRED: [ENGINE_FIELD], SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        SchemaKeyword.PROPERTIES: {
            Field.PURPOSE.value: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH},
            Field.DESCRIPTION.value: {SchemaKeyword.TYPE: SchemaType.STRING}, Field.METADATA.value: metadata_schema(),
            ENGINE_FIELD: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH},
            Field.SYSTEM_PROMPT.value: {SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL]},
            Field.COMPLETION.value: open_schema(COMPLETION_OWNER, category=OpenSchemaKind.PROVIDER),
            Field.ENGINE_OPTIONS.value: open_schema(OPTIONS_OWNER, category=OpenSchemaKind.IMPLEMENTATION),
            Field.INPUT_SCHEMA.value: {SchemaKeyword.ANY_OF: [{SchemaKeyword.TYPE: SchemaType.BOOLEAN}, open_schema(INPUT_SCHEMA_OWNER, category=OpenSchemaKind.SCHEMA)]},
            Field.OUTPUT_SCHEMA.value: {SchemaKeyword.ANY_OF: [{SchemaKeyword.TYPE: SchemaType.BOOLEAN}, open_schema(OUTPUT_SCHEMA_OWNER, category=OpenSchemaKind.SCHEMA)]},
            Field.TOOLS.value: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                      SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH}},
            Field.RESOURCES.value: {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT, SchemaKeyword.PROPERTIES: {
                ResourceField.PROMPT.value: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH},
                ResourceField.SKILLS.value: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                           SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH}},
                # 현재 RAG는 Project 단위 corpus다. 문서 ID 필터로 해석하지 않는다.
                ResourceField.RAG.value: {SchemaKeyword.TYPE: SchemaType.BOOLEAN},
                ResourceField.MCP.value: {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: {
                    SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.MIN_PROPERTIES: MINIMUM_RESOURCE_PROPERTIES,
                    SchemaKeyword.PROPERTY_NAMES: {SchemaKeyword.PATTERN: RESOURCE_NAME_PATTERN},
                    SchemaKeyword.ADDITIONAL_PROPERTIES: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: MINIMUM_TEXT_LENGTH}}},
            }},
            Field.POLICY.value: {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT, SchemaKeyword.PROPERTIES: {
                PolicyField.TIMEOUT_SECONDS.value: {SchemaKeyword.TYPE: SchemaType.NUMBER, SchemaKeyword.EXCLUSIVE_MINIMUM: MINIMUM_TIMEOUT_EXCLUSIVE},
                PolicyField.MAX_TOOL_CALLS.value: {SchemaKeyword.TYPE: SchemaType.INTEGER, SchemaKeyword.MINIMUM: MINIMUM_TOOL_CALLS},
                PolicyField.REQUIRE_TOOL.value: {SchemaKeyword.TYPE: SchemaType.BOOLEAN},
            }},
            Field.OUTPUT_FORMAT.value: {SchemaKeyword.ENUM: [OutputFormat.TEXT.value, OutputFormat.JSON.value]},
        },
    }


    @classmethod
    def purpose(cls, definition):
        return definition.get(cls.Field.PURPOSE.value, cls.NO_PURPOSE)

    @classmethod
    def engine(cls, definition):
        return definition[cls.ENGINE_FIELD]

    @classmethod
    def validate_selected(cls, definition):
        """Validate an already selected definition without inventing a stored Agent ID."""
        cls.validate(cls.SELECTED_VALIDATION_ID, definition)

    @classmethod
    def validate_mode(cls, definition, mode):
        if AgentMode(mode) == AgentMode.WORKFLOW:
            unsupported = set(definition) - cls.WORKFLOW_FIELDS
            if unsupported:
                raise ValueError(cls.UNSUPPORTED_WORKFLOW + cls.FIELD_SEPARATOR.join(sorted(unsupported)))

    @classmethod
    def identifier(cls, value):
        if not isinstance(value, str) or not re.fullmatch(cls.IDENTIFIER_PATTERN, value):
            raise ValueError(cls.INVALID_ID)
        return value

    @classmethod
    def revision(cls, definition):
        JsonObject.validate(definition)
        return hashlib.sha256(json.dumps(definition, ensure_ascii=cls.REVISION_ENSURE_ASCII, sort_keys=cls.REVISION_SORT_KEYS,
                                         allow_nan=JsonObject.ALLOW_NON_FINITE).encode(cls.REVISION_ENCODING)).hexdigest()

    @classmethod
    def validate(cls, identifier, definition):
        cls.identifier(identifier)
        JsonObject.validate(definition)
        errors = sorted(Draft202012Validator(cls.schema).iter_errors(definition),
                        key=lambda error: str(list(error.path)))
        if errors:
            error = errors[0]
            location = cls.ERROR_PATH_SEPARATOR.join(str(part) for part in error.path) or cls.RECORD_LOCATION
            raise ValueError(cls.INVALID_DEFINITION.format(identifier=identifier, location=location, message=error.message))
        for name in cls.JSON_SCHEMA_FIELDS:
            if name in definition:
                validate_local_refs(definition[name])
                Draft202012Validator.check_schema(definition[name])
        if definition.get(cls.Field.POLICY, {}).get(cls.PolicyField.REQUIRE_TOOL) and not (
                definition.get(cls.Field.TOOLS) or definition.get(cls.Field.RESOURCES, {}).get(cls.ResourceField.RAG)
                or definition.get(cls.Field.RESOURCES, {}).get(cls.ResourceField.MCP)):
            raise ValueError(cls.REQUIRED_TOOL)


class AgentPolicy:
    """Optional declared Agent ceilings; absence never creates a second limit."""

    NO_TIMEOUT = None
    NO_TOOL_LIMIT = None
    NO_REQUIRED_TOOL = None
    REQUIRED_COMPLETION = "Agent requires at least one successfully completed Tool"

    def __init__(self, value):
        self._value = deepcopy(value)

    @property
    def timeout_seconds(self):
        return self._value.get(AgentDefinition.PolicyField.TIMEOUT_SECONDS, self.NO_TIMEOUT)

    @property
    def max_tool_calls(self):
        return self._value.get(AgentDefinition.PolicyField.MAX_TOOL_CALLS, self.NO_TOOL_LIMIT)

    @property
    def require_tool(self):
        return self._value.get(AgentDefinition.PolicyField.REQUIRE_TOOL, self.NO_REQUIRED_TOOL)

    def validate_completion(self, completed):
        if self.require_tool and not completed:
            raise ValueError(self.REQUIRED_COMPLETION)


class AgentResourceSelection:
    """Presence-preserving view of the resources in an already validated definition."""

    NO_PROMPT = None
    NO_RAG = None

    def __init__(self, value):
        self._value = deepcopy(value)

    @property
    def skills(self):
        return deepcopy(self._value.get(AgentDefinition.ResourceField.SKILLS, []))

    @property
    def prompt(self):
        return self._value.get(AgentDefinition.ResourceField.PROMPT, self.NO_PROMPT)

    @property
    def mcp(self):
        return deepcopy(self._value.get(AgentDefinition.ResourceField.MCP, {}))

    @property
    def rag(self):
        return self._value.get(AgentDefinition.ResourceField.RAG, self.NO_RAG)

    @classmethod
    def replace_skills(cls, definition, skills):
        """Build an explicit detached edit; the owning mutation API still validates it."""
        value = deepcopy(definition)
        value.setdefault(AgentDefinition.Field.RESOURCES, {})[AgentDefinition.ResourceField.SKILLS] = deepcopy(skills)
        return value


class AgentProfile:
    """Validated detached definition for execution; serialization preserves omission."""

    DEFAULT_OUTPUT_FORMAT = AgentDefinition.OutputFormat.TEXT

    def __init__(self, identifier, definition):
        AgentDefinition.validate(identifier, definition)
        self._value = deepcopy(definition)

    @property
    def engine(self):
        return AgentDefinition.engine(self._value)

    @property
    def revision(self):
        return AgentDefinition.revision(self._value)

    @property
    def tools(self):
        return deepcopy(self._value.get(AgentDefinition.Field.TOOLS, []))

    @property
    def policy(self):
        return AgentPolicy(self._value.get(AgentDefinition.Field.POLICY, {}))

    @property
    def resources(self):
        return AgentResourceSelection(self._value.get(AgentDefinition.Field.RESOURCES, {}))

    @property
    def output_format(self):
        return self._value.get(AgentDefinition.Field.OUTPUT_FORMAT, self.DEFAULT_OUTPUT_FORMAT)

    def validate_input(self, value):
        if AgentDefinition.Field.INPUT_SCHEMA in self._value:
            Draft202012Validator(self._value[AgentDefinition.Field.INPUT_SCHEMA]).validate(value)

    def validate_output(self, value):
        if AgentDefinition.Field.OUTPUT_SCHEMA in self._value:
            Draft202012Validator(self._value[AgentDefinition.Field.OUTPUT_SCHEMA]).validate(value)

    @property
    def has_input_schema(self):
        return AgentDefinition.Field.INPUT_SCHEMA in self._value

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, init=False)
class AgentRecord:
    """Validated storage snapshot; wire dictionaries never escape through properties."""

    STORAGE_VERSION = 1
    PROJECT_PATTERN = r"[a-f0-9]{32}"
    INVALID_PROJECT = "Invalid Agent Project ID"
    INVALID_FIELDS = "Agent record requires all current fields"
    INVALID_VERSION = "Unsupported Agent storage_version"
    INVALID_IDENTITY = "Agent record identity or revision mismatch"

    class Field(StrEnum):
        STORAGE_VERSION = "storage_version"
        PROJECT_ID = "project_id"
        ID = "id"
        DEFINITION = "definition"
        REVISION = "revision"

    VALUE_ATTRIBUTE = "_value"

    _value: dict

    @classmethod
    def validate_project_id(cls, value):
        if not isinstance(value, str) or not re.fullmatch(cls.PROJECT_PATTERN, value):
            raise ValueError(cls.INVALID_PROJECT)
        return value

    @classmethod
    def create(cls, project_id, identifier, definition):
        cls.validate_project_id(project_id)
        AgentDefinition.validate(identifier, definition)
        result = object.__new__(cls)
        object.__setattr__(result, cls.VALUE_ATTRIBUTE, {
            cls.Field.STORAGE_VERSION: cls.STORAGE_VERSION, cls.Field.PROJECT_ID: project_id,
            cls.Field.ID: identifier, cls.Field.DEFINITION: deepcopy(definition),
            cls.Field.REVISION: AgentDefinition.revision(definition)})
        return result

    @classmethod
    def read(cls, value, *, project_id, identifier):
        if not isinstance(value, dict) or value.keys() != set(cls.Field):
            raise ValueError(cls.INVALID_FIELDS)
        version = value[cls.Field.STORAGE_VERSION]
        if type(version) is not int or version != cls.STORAGE_VERSION:
            raise ValueError(cls.INVALID_VERSION)
        expected = cls.create(project_id, identifier, value[cls.Field.DEFINITION])
        if value != expected.to_dict():
            raise ValueError(cls.INVALID_IDENTITY)
        return expected

    @property
    def project_id(self):
        return self._value[self.Field.PROJECT_ID]

    @property
    def id(self):
        return self._value[self.Field.ID]

    @property
    def definition(self):
        return deepcopy(self._value[self.Field.DEFINITION])

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True)
class AgentCatalog:
    """Mandatory Project inventory; missing evidence never becomes an empty catalogue."""

    SCHEMA_VERSION = 1
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    RECORDS_ATTRIBUTE = "records"
    INVALID_FIELDS = "Agent catalog requires all current fields"
    INVALID_VERSION = "Unsupported Agent catalog schema_version"
    INVALID_OWNER = "Invalid Agent catalog owner or records"
    INVALID_DIGEST = "Invalid Agent record digest"

    schema_version: int
    project_id: str
    records: Mapping[str, str]

    def __post_init__(self):
        AgentRecord.validate_project_id(self.project_id)
        if type(self.schema_version) is not int or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError(self.INVALID_VERSION)
        if not isinstance(self.records, Mapping):
            raise ValueError(self.INVALID_OWNER)
        for identifier, digest in self.records.items():
            AgentDefinition.identifier(identifier)
            if not isinstance(digest, str) or not re.fullmatch(self.DIGEST_PATTERN, digest):
                raise ValueError(self.INVALID_DIGEST)
        object.__setattr__(self, self.RECORDS_ATTRIBUTE, MappingProxyType(dict(self.records)))

    @classmethod
    def empty(cls, project_id):
        return cls(cls.SCHEMA_VERSION, project_id, {})

    @classmethod
    def read(cls, value, *, project_id):
        AgentRecord.validate_project_id(project_id)
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        JsonObject.validate(value)
        result = cls(**value)
        if result.project_id != project_id:
            raise ValueError(cls.INVALID_OWNER)
        return result

    def with_record(self, identifier, digest):
        return replace(self, records={**self.records, identifier: digest})

    def without_record(self, identifier):
        records = dict(self.records)
        del records[identifier]
        return replace(self, records=records)

    def to_dict(self):
        return {item.name: dict(value) if isinstance(value, Mapping) else value
                for item in fields(self) for value in (getattr(self, item.name),)}


@dataclass(frozen=True, init=False)
class AgentBinding:
    """Immutable admitted evidence, with detached definition and wire projections."""

    SCHEMA_VERSION = 1
    METADATA_FIELD = "agent_binding"
    CONTEXT_FIELD = "request_context"
    EXECUTION_FIELD = "agent_execution"
    INVALID_FIELDS = "Agent execution binding requires all current evidence"
    INVALID_VERSION = "Unsupported Agent binding schema_version"
    INVALID_IDENTITY = "Agent binding identity, revision or Engine mismatch"
    MISSING_BINDING = "Run is missing its original Agent binding"
    RUN_MISMATCH = "Run Agent and Engine binding mismatch"
    IMMUTABLE_EVIDENCE = "Run Agent evidence is immutable"

    class Field(StrEnum):
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        AGENT_ID = "agent_id"
        DEFINITION = "definition"
        REVISION = "revision"
        ENGINE = "engine"

    VALUE_ATTRIBUTE = "_value"

    _value: dict

    @classmethod
    def create(cls, project_id, identifier, definition):
        record = AgentRecord.create(project_id, identifier, definition)
        result = object.__new__(cls)
        object.__setattr__(result, cls.VALUE_ATTRIBUTE, {
            cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION, cls.Field.PROJECT_ID: record.project_id,
            cls.Field.AGENT_ID: record.id, cls.Field.DEFINITION: record.definition,
            cls.Field.REVISION: record.revision, cls.Field.ENGINE: AgentDefinition.engine(definition)})
        return result

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != set(cls.Field):
            raise ValueError(cls.INVALID_FIELDS)
        version = value[cls.Field.SCHEMA_VERSION]
        if type(version) is not int or version != cls.SCHEMA_VERSION:
            raise ValueError(cls.INVALID_VERSION)
        expected = cls.create(value[cls.Field.PROJECT_ID], value[cls.Field.AGENT_ID], value[cls.Field.DEFINITION])
        if value != expected.to_dict():
            raise ValueError(cls.INVALID_IDENTITY)
        return expected

    @property
    def project_id(self):
        return self._value[self.Field.PROJECT_ID]

    @property
    def agent_id(self):
        return self._value[self.Field.AGENT_ID]

    @property
    def definition(self):
        return deepcopy(self._value[self.Field.DEFINITION])

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    @property
    def engine(self):
        return self._value[self.Field.ENGINE]

    def to_dict(self):
        return deepcopy(self._value)

    @classmethod
    def metadata(cls, binding, request_context=UNSET):
        value = {cls.METADATA_FIELD: cls.read(binding.to_dict()).to_dict()}
        if request_context is not UNSET:
            value[cls.CONTEXT_FIELD] = deepcopy(request_context)
        return value

    @classmethod
    def store_request_context(cls, metadata, value):
        metadata[cls.CONTEXT_FIELD] = deepcopy(value)

    @classmethod
    def has_execution(cls, metadata):
        return cls.EXECUTION_FIELD in metadata

    @classmethod
    def execution(cls, metadata):
        return metadata[cls.EXECUTION_FIELD]

    @classmethod
    def store_execution(cls, metadata, value):
        metadata[cls.EXECUTION_FIELD] = deepcopy(value)

    @classmethod
    def request_context(cls, metadata, *, default=UNSET):
        return metadata.get(cls.CONTEXT_FIELD, default)

    @classmethod
    def from_metadata(cls, metadata):
        if cls.METADATA_FIELD not in metadata:
            raise ValueError(cls.MISSING_BINDING)
        return cls.read(metadata[cls.METADATA_FIELD])

    @classmethod
    def from_run(cls, run):
        binding = cls.from_metadata(run.metadata)
        if binding.agent_id != run.agent_id or binding.engine != run.engine:
            raise ValueError(cls.RUN_MISMATCH)
        return binding

    @classmethod
    def definition_from_metadata(cls, metadata):
        return cls.from_metadata(metadata).definition

    @classmethod
    def same_request(cls, left, right):
        return cls.metadata(cls.from_metadata(left), cls.request_context(left)) == cls.metadata(
            cls.from_metadata(right), cls.request_context(right))

    @classmethod
    def validate_run_update(cls, prior, current):
        if (prior.agent_id != current.agent_id or prior.engine != current.engine
                or not cls.same_request(prior.metadata, current.metadata)
                or (cls.EXECUTION_FIELD in prior.metadata
                    and prior.metadata[cls.EXECUTION_FIELD] != current.metadata.get(cls.EXECUTION_FIELD))):
            raise ValueError(cls.IMMUTABLE_EVIDENCE)
