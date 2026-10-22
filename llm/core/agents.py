"""Independent Agent definition and current storage envelopes; no execution state."""

from copy import deepcopy
import hashlib
import json
import re
from enum import StrEnum

from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.schema import open_schema, metadata_schema, validate_local_refs
from llm.core.configuration import UNSET


class AgentMode(StrEnum):
    BEHAVIORAL = "behavioral"
    WORKFLOW = "workflow"


class AgentDefinition:
    """A selected Engine is required; application descriptions remain optional."""

    schema = {
        "type": "object", "required": ["engine"], "additionalProperties": False,
        "properties": {
            "purpose": {"type": "string", "minLength": 1},
            "description": {"type": "string"}, "metadata": metadata_schema(),
            "engine": {"type": "string", "minLength": 1},
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
        ProjectConfig.validate_json(definition)
        return hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode("utf-8")).hexdigest()

    @classmethod
    def validate(cls, identifier, definition):
        cls.identifier(identifier)
        ProjectConfig.validate_json(definition)
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


class AgentRecord:
    """Current identity, definition and revision are all required on every read."""

    STORAGE_VERSION = 1

    @staticmethod
    def project_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("Invalid Agent Project ID")
        return value

    @classmethod
    def create(cls, project_id, identifier, definition):
        cls.project_id(project_id)
        AgentDefinition.validate(identifier, definition)
        return {"storage_version": cls.STORAGE_VERSION, "project_id": project_id,
                "id": identifier, "definition": deepcopy(definition),
                "revision": AgentDefinition.revision(definition)}

    @classmethod
    def read(cls, value, *, project_id, identifier):
        if not isinstance(value, dict) or value.keys() != {
                "storage_version", "project_id", "id", "definition", "revision"}:
            raise ValueError("Agent record requires all current fields")
        if type(value["storage_version"]) is not int or value["storage_version"] != cls.STORAGE_VERSION:
            raise ValueError("Unsupported Agent storage_version")
        expected = cls.create(project_id, identifier, value["definition"])
        if value != expected:
            raise ValueError("Agent record identity or revision mismatch")
        return deepcopy(value)


class AgentCatalog:
    """Mandatory Project inventory: lost records never mean an empty catalogue."""

    SCHEMA_VERSION = 1

    @classmethod
    def empty(cls, project_id):
        AgentRecord.project_id(project_id)
        return {"schema_version": cls.SCHEMA_VERSION, "project_id": project_id, "records": {}}

    @classmethod
    def validate(cls, value, *, project_id):
        AgentRecord.project_id(project_id)
        if not isinstance(value, dict) or value.keys() != {"schema_version", "project_id", "records"}:
            raise ValueError("Agent catalog requires all current fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Unsupported Agent catalog schema_version")
        if value["project_id"] != project_id or not isinstance(value["records"], dict):
            raise ValueError("Invalid Agent catalog owner or records")
        for identifier, digest in value["records"].items():
            AgentDefinition.identifier(identifier)
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid Agent record digest")


class AgentBinding:
    """Immutable admitted identity; missing evidence is never filled from a current definition."""

    SCHEMA_VERSION = 1
    METADATA_FIELD = "agent_binding"
    CONTEXT_FIELD = "request_context"
    EXECUTION_FIELD = "agent_execution"

    @classmethod
    def metadata(cls, binding, request_context=UNSET):
        value = {cls.METADATA_FIELD: cls.read(binding)}
        if request_context is not UNSET:
            value[cls.CONTEXT_FIELD] = deepcopy(request_context)
        return value

    @classmethod
    def request_context(cls, metadata):
        return metadata.get(cls.CONTEXT_FIELD, UNSET)

    @classmethod
    def from_metadata(cls, metadata):
        if cls.METADATA_FIELD not in metadata:
            raise ValueError("Run is missing its original Agent binding")
        return cls.read(metadata[cls.METADATA_FIELD])

    @classmethod
    def from_run(cls, run):
        binding = cls.from_metadata(run.metadata)
        if binding["agent_id"] != run.agent_id or binding["engine"] != run.engine:
            raise ValueError("Run Agent and Engine binding mismatch")
        return binding

    @classmethod
    def definition_from_metadata(cls, metadata):
        """Detached admitted definition for read-only consumers such as naming."""
        return cls.from_metadata(metadata)["definition"]

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
            raise ValueError("Run Agent evidence is immutable")

    @classmethod
    def create(cls, project_id, identifier, definition):
        record = AgentRecord.create(project_id, identifier, definition)
        return {"schema_version": cls.SCHEMA_VERSION, "project_id": record["project_id"],
                "agent_id": record["id"], "definition": record["definition"],
                "revision": record["revision"], "engine": definition["engine"]}

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != {
                "schema_version", "project_id", "agent_id", "definition", "revision", "engine"}:
            raise ValueError("Agent execution binding requires all current evidence")
        if type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Unsupported Agent binding schema_version")
        expected = cls.create(value["project_id"], value["agent_id"], value["definition"])
        if value != expected:
            raise ValueError("Agent binding identity, revision or Engine mismatch")
        return deepcopy(value)
