"""Workflow의 JSON 입출력 계약. 저장 검증과 실행기가 같은 포인터/스키마 규칙을 쓴다."""

from copy import deepcopy

from jsonschema import Draft202012Validator


def pointer_parts(path: str) -> list[str]:
    """RFC 6901 포인터를 해석한다. 표현식이나 Python 코드는 실행하지 않는다."""
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("Binding path must be a JSON Pointer")
    parts = path[1:].split("/") if path else []
    if any("~" in part.replace("~1", "").replace("~0", "") for part in parts):
        raise ValueError("Invalid JSON Pointer escape")
    return [part.replace("~1", "/").replace("~0", "~") for part in parts]


def read_pointer(document, path: str):
    """누락된 필드는 명시적 오류다. null과 누락을 구분한다."""
    value = document
    for part in pointer_parts(path):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif (isinstance(value, list) and part.isascii() and part.isdecimal()
              and (part == "0" or not part.startswith("0")) and int(part) < len(value)):
            value = value[int(part)]
        else:
            raise KeyError(f"Missing binding path: {path}")
    return value


def validate_contract(definition: dict) -> None:
    """매핑과 JSON Schema를 저장 전에 검사한다. 외부 스키마 조회는 허용하지 않는다."""
    for direction in ("inputs", "outputs"):
        if direction not in definition:
            continue
        mapping = definition[direction]
        if not isinstance(mapping, dict):
            raise ValueError(f"{direction} must be an object")
        for name, path in mapping.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Binding names must be nonempty strings")
            pointer_parts(path)

    def check_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef") and (
                        not isinstance(item, str) or not item.startswith("#")):
                    raise ValueError("Workflow schemas only support local references")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    for name in ("input_schema", "output_schema", "resume_schema"):
        if name in definition:
            check_refs(definition[name])
            Draft202012Validator.check_schema(definition[name])


def bind(mapping: dict, source: dict) -> dict:
    """원본과 분리된 최상위 키 객체를 만든다. 목적 키의 암묵적인 중첩 쓰기는 없다."""
    return {name: deepcopy(read_pointer(source, path)) for name, path in mapping.items()}


def validate_value(definition: dict, direction: str, value: dict) -> None:
    """입력은 호출 전, 출력은 상태 병합 전에 검증한다."""
    schema = definition.get(f"{direction}_schema")
    if schema is not None:
        Draft202012Validator(schema).validate(value)
