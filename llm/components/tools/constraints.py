"""Project-owned Tool 인자 제약. 원본 JSON Schema와 교집합을 만들며 값을 추천하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator
from llm.core.json_data import JsonObject
from llm.core.policies import ArgumentConstraint, ConstraintMode
from llm.core.schema import SchemaKeyword, SchemaInput


class ConstraintError:
    UNDECLARED_PARAMETER = "Constraint requires a declared Tool parameter: {name}"


def constrained_parameters(schema: dict, fields: dict) -> dict:
    """로컬 $defs와 원본 제약을 보존한다. 공개 스키마를 수정하지 않는다."""
    result = deepcopy(schema)
    validator = Draft202012Validator(schema)
    for name, value in fields.items():
        rule = ArgumentConstraint(value)
        original = schema.get(SchemaKeyword.PROPERTIES, {}).get(name)
        if original is SchemaInput.UNSPECIFIED:
            raise ValueError(ConstraintError.UNDECLARED_PARAMETER.format(name=name))
        for choice in rule.choices:
            validator.evolve(schema=original).validate(choice)
        # allOf keeps enum, exclusive bounds, $ref, conditional and type constraints intact.
        result[SchemaKeyword.PROPERTIES][name] = {SchemaKeyword.ALL_OF: [deepcopy(original), rule.value_schema()]}
        # Omission must not escape to a handler/component's unconstrained default.
        # Only fixed owns an explicit replacement value; other modes require input.
        if rule.mode != ConstraintMode.FIXED and name not in result.get(SchemaKeyword.REQUIRED, []):
            result.setdefault(SchemaKeyword.REQUIRED, []).append(name)
    return result


def constrained_arguments(schema: dict, values: dict, fields: dict) -> dict:
    JsonObject.validate(values)
    result = deepcopy(values)
    for key, value in fields.items():
        rule = ArgumentConstraint(value)
        if rule.mode == ConstraintMode.FIXED and key not in result:
            result[key] = rule.choices[0]
    Draft202012Validator(constrained_parameters(schema, fields)).validate(result)
    return result
