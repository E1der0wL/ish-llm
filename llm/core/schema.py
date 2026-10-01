"""UI에서 공유할 JSON Schema. 공급자의 열린 인자를 닫힌 목록으로 제한하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator


def field(kind, description="", **constraints):
    return {"type": kind, "description": description, **constraints}


def object_schema(properties=None, **extra):
    return {"type": "object", "properties": properties or {}, "additionalProperties": True, **extra}


def completion_schema():
    return object_schema({
        "model": {"type": "string", "minLength": 1, "description": "공급자/모델 이름"},
        "api_key": {"type": ["string", "null"], "description": "공급자 인증 인자"},
        "api_base": {"type": ["string", "null"], "description": "공급자 API 주소"},
        "temperature": {"type": ["number", "null"], "description": "공급자가 지원하는 생성 온도"},
        "max_tokens": {"type": ["integer", "null"], "minimum": 1, "description": "최대 출력 토큰"},
        "max_completion_tokens": {"type": ["integer", "null"], "minimum": 1},
        "top_p": {"type": ["number", "null"]}, "seed": {"type": ["integer", "null"]},
        "timeout": {"type": ["number", "null"], "exclusiveMinimum": 0},
        "num_retries": {"type": ["integer", "null"], "minimum": 0},
        "max_retries": {"type": ["integer", "null"], "minimum": 0},
        "response_format": object_schema(type=["object", "null"]),
    }, description="알려진 공통 인자와 추가 JSON 인자를 허용한다. 실제 지원 여부는 공급자/모델에 따른다.",
       **{"x-open-parameters": True})


def checked_schema(value):
    """UI 스키마는 JSON이며 외부 스키마를 내려받지 않는다."""
    import json
    json.dumps(value, allow_nan=False)
    def check(item, mapping=False):
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "default" and not mapping:
                    raise ValueError("Configuration schemas must not provide implicit defaults")
                if key in ("$ref", "$dynamicRef") and not str(child).startswith("#"):
                    raise ValueError("Only local configuration schema references are supported")
                if key not in ("const", "enum", "examples"):
                    check(child, not mapping and key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"))
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)
    Draft202012Validator.check_schema(value)
    return deepcopy(value)
