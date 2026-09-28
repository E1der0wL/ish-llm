"""재사용 가능한 업무 정의. 실행 상태는 Agent가 아니라 소유 Run/Step에 둔다."""

import hashlib
import json
from jsonschema import Draft202012Validator
from llm.components.definitions import DefinitionComponent
from .data import AgentData


class AgentComponent(DefinitionComponent):
    """엔진 선택, 업무 계약, 리소스와 정책을 열린 JSON으로 저장한다."""

    name = "agents"
    directory = "agents"
    capabilities = ("agents",)
    data_class = AgentData
    schema = {
        "type": "object", "required": ["purpose", "engine"],
        "properties": {
            "purpose": {"type": "string", "minLength": 1},
            "engine": {"type": "string", "minLength": 1},
            "system_prompt": {"type": "string"},
            "completion": {
                "type": "object", "required": ["model"],
                "properties": {"model": {"type": "string", "minLength": 1}},
            },
            "engine_options": {"type": "object"},
            "tools": {"type": "array", "uniqueItems": True,
                      "items": {"type": "string", "minLength": 1}},
            "resources": {"type": "object", "properties": {
                "skills": {"type": "array", "uniqueItems": True,
                           "items": {"type": "string", "minLength": 1}},
                # 현재 RAG는 Project 단위 corpus다. 문서 ID 필터로 해석하지 않는다.
                "rag": {"type": "boolean"},
                "mcp": {"type": "object", "additionalProperties": {
                    "type": "object", "minProperties": 1,
                    "propertyNames": {"pattern": "^[A-Za-z0-9_-]{1,64}$"},
                    "additionalProperties": {"type": "string", "minLength": 1}}},
            }},
            "policy": {"type": "object", "properties": {
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                "max_tool_calls": {"type": "integer", "minimum": 1},
                "require_tool": {"type": "boolean"},
            }},
            "output_format": {"enum": ["text", "json"]},
        },
    }

    @staticmethod
    def revision(data: dict) -> str:
        """같은 JSON 정의는 동일한 버전 지문을 가진다."""
        return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                         allow_nan=False).encode("utf-8")).hexdigest()

    def validate_record(self, identifier: str, data: dict) -> None:
        super().validate_record(identifier, data)
        # 제거된 필드를 조용히 무시하거나 과거 형식으로 자동 변환하지 않는다.
        if any(key in data for key in ("loop", "skills", "rag", "mcp")):
            raise ValueError("Use engine_options and resources in Agent definitions")
        def check_refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in ("$ref", "$dynamicRef") and (not isinstance(item, str) or not item.startswith("#")):
                        raise ValueError("Agent schemas only support local references")
                    check_refs(item)
            elif isinstance(value, list):
                for item in value:
                    check_refs(item)
        for name in ("input_schema", "output_schema"):
            if name in data:
                check_refs(data[name])
                Draft202012Validator.check_schema(data[name])
        if data.get("policy", {}).get("require_tool") and not (
                data.get("tools") or data.get("resources", {}).get("rag") or data.get("resources", {}).get("mcp")):
            raise ValueError("require_tool needs at least one allowed Tool")
