"""Project-owned Agent definitions; validation does not activate Components or run Engines."""

import hashlib
import json
import re

from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.schema import open_schema, metadata_schema, validate_local_refs


class AgentDefinition:
    """The only mandatory execution property is the selected Engine name."""

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
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("Invalid Agent ID")
        return value

    @staticmethod
    def revision(data):
        # Preserve pre-domain Agent binding fingerprints exactly.
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode("utf-8")).hexdigest()

    @classmethod
    def validate(cls, identifier, data):
        cls.identifier(identifier)
        ProjectConfig.validate_json(data)
        errors = sorted(Draft202012Validator(cls.schema).iter_errors(data),
                        key=lambda error: str(list(error.path)))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.path) or "record"
            raise ValueError(f"agents/{identifier}: {location}: {error.message}")
        for name in ("input_schema", "output_schema"):
            if name in data:
                validate_local_refs(data[name])
                Draft202012Validator.check_schema(data[name])
        if data.get("policy", {}).get("require_tool") and not (
                data.get("tools") or data.get("resources", {}).get("rag") or data.get("resources", {}).get("mcp")):
            raise ValueError("require_tool needs at least one allowed Tool")
