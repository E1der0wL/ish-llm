"""UI에서 공유할 JSON Schema. 공급자의 열린 인자를 닫힌 목록으로 제한하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator


def field(kind, description="", **constraints):
    return {"type": kind, "description": description, **constraints}


def object_schema(properties=None, **extra):
    return {"type": "object", "properties": properties or {}, "additionalProperties": False, **extra}


def open_schema(owner, *, category, properties=None, **extra):
    """교체 가능한 구현/외부 데이터의 소유자를 명시한다. backend 설정 기본값은 아니다."""
    if not isinstance(owner, str) or not owner.strip() or category not in (
            "metadata", "provider", "implementation", "adapter", "result", "schema", "data"):
        raise ValueError("Open schema requires an explicit semantic owner and category")
    return object_schema(properties, additionalProperties=True,
                         **{"x-schema-owner": owner, "x-open-kind": category}, **extra)


def metadata_schema():
    """Application 데이터. 실행 권한이나 구현체 설정으로 읽지 않는다."""
    return open_schema("application.metadata", category="metadata")


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


def mark_host_overrides(schema, values):
    """병합과 같은 leaf 단위로 고정값을 표시한다. 빈 dict는 덮어쓰는 값이 없다."""
    if isinstance(values, dict):
        if values and "type" not in schema and not any(key in schema for key in ("$ref", "allOf", "anyOf", "oneOf")):
            # 선택 구현체의 opaque 인자에 대한 UI 관찰이다. Backend가 새 옵션 계약을 만들지 않는다.
            schema.update(open_schema("host-supplied selected implementation value", category="implementation"))
        for key, value in values.items():
            mark_host_overrides(schema.setdefault("properties", {}).setdefault(key, {}), value)
    else:
        schema["x-host-override"] = True


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
