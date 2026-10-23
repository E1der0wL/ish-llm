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
from llm.core.schema import open_schema, metadata_schema, validate_local_refs
from llm.core.configuration import UNSET


class AgentMode(StrEnum):
    BEHAVIORAL = "behavioral"
    WORKFLOW = "workflow"


class AgentDefinition:
    """A selected Engine is required; application descriptions remain optional."""

    ENGINE_FIELD = "engine"

    schema = {
        "type": "object", "required": [ENGINE_FIELD], "additionalProperties": False,
        "properties": {
            "purpose": {"type": "string", "minLength": 1},
            "description": {"type": "string"}, "metadata": metadata_schema(),
            ENGINE_FIELD: {"type": "string", "minLength": 1},
            "system_prompt": {"type": ["string", "null"]},
            "completion": open_schema("selected Agent Engine completion adapter", category="provider"),
            "engine_options": open_schema("selected Agent Engine.for_agent", category="implementation"),
            "input_schema": {"anyOf": [{"type": "boolean"}, open_schema("Agent input JSON Schema", category="schema")]},
            "output_schema": {"anyOf": [{"type": "boolean"}, open_schema("Agent output JSON Schema", category="schema")]},
            "tools": {"type": "array", "uniqueItems": True,
                      "items": {"type": "string", "minLength": 1}},
            "resources": {"type": "object", "additionalProperties": False, "properties": {
                "prompt": {"type": "string", "minLength": 1},
                "skills": {"type": "array", "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1}},
                # 현재 RAG는 Project 단위 corpus다. 문서 ID 필터로 해석하지 않는다.
                "rag": {"type": "boolean"},
                "mcp": {"type": "object", "additionalProperties": {
                    "type": "object", "minProperties": 1,
                    "propertyNames": {"pattern": "^[A-Za-z0-9_-]{1,64}$"},
                    "additionalProperties": {"type": "string", "minLength": 1}}},
            }},
            "policy": {"type": "object", "additionalProperties": False, "properties": {
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                "max_tool_calls": {"type": "integer", "minimum": 1},
                "require_tool": {"type": "boolean"},
            }},
            "output_format": {"enum": ["text", "json"]},
        },
    }


    @classmethod
    def engine(cls, definition):
        return definition[cls.ENGINE_FIELD]

    @staticmethod
    def validate_mode(definition, mode):
        if AgentMode(mode) == AgentMode.WORKFLOW:
            unsupported = set(definition) - {"purpose", "description", "engine", "engine_options", "metadata"}
            if unsupported:
                raise ValueError("Graph Agent only orchestrates Workflows; unsupported fields: "
                                 + ", ".join(sorted(unsupported)))

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("Invalid Agent ID")
        return value

    @staticmethod
    def revision(definition):
        JsonObject.validate(definition)
        return hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode("utf-8")).hexdigest()

    @classmethod
    def validate(cls, identifier, definition):
        cls.identifier(identifier)
        JsonObject.validate(definition)
        errors = sorted(Draft202012Validator(cls.schema).iter_errors(definition),
                        key=lambda error: str(list(error.path)))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.path) or "record"
            raise ValueError(f"agents/{identifier}: {location}: {error.message}")
        for name in ("input_schema", "output_schema"):
            if name in definition:
                validate_local_refs(definition[name])
                Draft202012Validator.check_schema(definition[name])
        if definition.get("policy", {}).get("require_tool") and not (
                definition.get("tools") or definition.get("resources", {}).get("rag")
                or definition.get("resources", {}).get("mcp")):
            raise ValueError("require_tool needs at least one allowed Tool")


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
        object.__setattr__(result, "_value", {
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
        object.__setattr__(self, "records", MappingProxyType(dict(self.records)))

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

    _value: dict

    @classmethod
    def create(cls, project_id, identifier, definition):
        record = AgentRecord.create(project_id, identifier, definition)
        result = object.__new__(cls)
        object.__setattr__(result, "_value", {
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
    def request_context(cls, metadata):
        return metadata.get(cls.CONTEXT_FIELD, UNSET)

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
