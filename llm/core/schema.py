"""UI에서 공유할 JSON Schema. 공급자의 열린 인자를 닫힌 목록으로 제한하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator


def field(kind, default=None, description="", **constraints):
    return {"type": kind, "default": default, "description": description, **constraints}


def object_schema(properties=None, **extra):
    return {"type": "object", "properties": properties or {}, "additionalProperties": True, **extra}


def schema_from_default(value):
    """기본 설정에 선언된 키를 폼 힌트로 제공한다. null/빈 배열은 값 타입을 추측하지 않는다."""
    if isinstance(value, dict):
        return object_schema({key: schema_from_default(item) for key, item in value.items()}, default=deepcopy(value))
    if isinstance(value, list):
        return {"type": "array", "default": deepcopy(value)}
    if value is None:
        return {"default": None, "description": "컴포넌트가 허용 타입을 별도로 선언하지 않았음"}
    kind = "boolean" if type(value) is bool else "integer" if type(value) is int else "number" if type(value) is float else "string"
    return field(kind, deepcopy(value), **{"x-inferred-from-default": True})


def completion_schema():
    return object_schema({
        "model": {"type": "string", "minLength": 1, "description": "공급자/모델 이름"},
        "api_key": {"type": "string", "description": "공급자 인증 인자"},
        "api_base": {"type": ["string", "null"], "description": "공급자 API 주소"},
        "temperature": {"type": "number", "description": "공급자가 지원하는 생성 온도"},
        "max_tokens": {"type": "integer", "minimum": 1, "description": "최대 출력 토큰"},
        "max_completion_tokens": {"type": "integer", "minimum": 1},
        "top_p": {"type": "number"}, "seed": {"type": "integer"},
        "timeout": {"type": "number", "exclusiveMinimum": 0},
        "num_retries": {"type": "integer", "minimum": 0},
        "response_format": object_schema(),
    }, description="알려진 공통 인자와 추가 JSON 인자를 허용한다. 실제 지원 여부는 공급자/모델에 따른다.",
       **{"x-open-parameters": True})


def checked_schema(value):
    """UI 스키마는 JSON이며 외부 스키마를 내려받지 않는다."""
    import json
    json.dumps(value, allow_nan=False)
    def check(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("$ref", "$dynamicRef") and not str(child).startswith("#"):
                    raise ValueError("Only local configuration schema references are supported")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
    check(value)
    Draft202012Validator.check_schema(value)
    return deepcopy(value)
