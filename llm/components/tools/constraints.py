"""Project-owned Tool 인자 제약. 원본 JSON Schema와 교집합을 만들며 값을 추천하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.policies import ArgumentConstraint, ConstraintMode


def constrained_parameters(schema: dict, fields: dict) -> dict:
    """로컬 $defs와 원본 제약을 보존한다. 공개 스키마를 수정하지 않는다."""
    result = deepcopy(schema)
    validator = Draft202012Validator(schema)
    for name, value in fields.items():
        rule = ArgumentConstraint(value)
        original = schema.get("properties", {}).get(name)
        if original is None:
            raise ValueError(f"Constraint requires a declared Tool parameter: {name}")
        for choice in rule.choices:
            validator.evolve(schema=original).validate(choice)
        # allOf keeps enum, exclusive bounds, $ref, conditional and type constraints intact.
        result["properties"][name] = {"allOf": [deepcopy(original), rule.value_schema()]}
        # Omission must not escape to a handler/component's unconstrained default.
        # Only fixed owns an explicit replacement value; other modes require input.
        if rule.mode != ConstraintMode.FIXED and name not in result.get("required", []):
            result.setdefault("required", []).append(name)
    return result


def constrained_arguments(schema: dict, values: dict, fields: dict) -> dict:
    ProjectConfig.validate_json(values)
    if not isinstance(values, dict):
        raise ValueError("Tool arguments must be an object")
    result = deepcopy(values)
    for key, value in fields.items():
        rule = ArgumentConstraint(value)
        if rule.mode == ConstraintMode.FIXED and key not in result:
            result[key] = rule.choices[0]
    Draft202012Validator(constrained_parameters(schema, fields)).validate(result)
    return result
