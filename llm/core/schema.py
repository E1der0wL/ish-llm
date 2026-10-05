"""UI에서 공유할 JSON Schema. 공급자의 열린 인자를 닫힌 목록으로 제한하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator


def field(kind, description="", **constraints):
    return {"type": kind, "description": description, **constraints}


def object_schema(properties=None, **extra):
    return {"type": "object", "properties": properties or {}, "additionalProperties": True, **extra}


def implementation_schema(*, config=None, policy=None, **metadata):
    """구현체 설정의 공통 외형. 필드 의미·필수값은 구현체가 선언하며 값은 만들지 않는다."""
    return object_schema({"config": object_schema() if config is None else config,
                          "policy": object_schema() if policy is None else policy},
                         additionalProperties=False, **metadata)


def validate_implementation_settings(value, *, scope="implementation"):
    """저장·직접 설정 경계에서 평면 설정과 null 컨테이너를 거부한다."""
    if not isinstance(value, dict):
        raise TypeError(f"{scope} settings must be an object")
    extra = value.keys() - {"config", "policy"}
    if extra:
        raise ValueError(f"{scope}: settings belong under config/policy: {sorted(extra)}")
    for key in ("config", "policy"):
        if key in value and not isinstance(value[key], dict):
            raise TypeError(f"{scope}.{key} must be an object; omit an unset section")


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


def checked_implementation_schema(value):
    """확장 구현체도 같은 설정 외형을 공개한다. 각 section의 내용은 구현체가 결정한다."""
    spec = checked_schema(value)
    if not isinstance(spec, dict) or spec.get("type") != "object":
        raise ValueError("Implementation configuration schema must describe an object")
    if spec.get("properties", {}).keys() - {"config", "policy"}:
        raise ValueError("Implementation configuration schema fields belong under config/policy")
    for name, section in spec.get("properties", {}).items():
        if not isinstance(section, dict) or section.get("type") != "object":
            raise ValueError(f"Implementation {name} schema must describe a non-null object")
    # 외형 제한을 schema에도 담아 ProjectConfig와 UI 검증이 일치하게 한다.
    spec["additionalProperties"] = False
    return spec
