"""Project-owned Tool 인자 제약. 원본 JSON Schema와 교집합을 만들며 값을 추천하지 않는다."""

from copy import deepcopy
from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.interactions import same_interaction_value
from llm.core.policies import validate_tool_constraints


def rule_schema(rule: dict) -> dict:
    if rule["mode"] == "fixed":
        return {"const": deepcopy(rule["value"])}
    if rule["mode"] == "selectable":
        return {"enum": deepcopy(rule["values"])}
    return {"type": "number", **{k: v for k, v in rule.items() if k != "mode"}}


def narrow_constraints(parent: dict, child: dict) -> dict:
    """생략은 상속이다. 허용 범위를 넓히거나 증명할 수 없는 교차 모드는 거부한다."""
    validate_tool_constraints(child)
    result = deepcopy(parent)
    for name, fields in child.items():
        for key, rule in fields.items():
            old = parent.get(name, {}).get(key)
            narrowed = deepcopy(rule)
            if old is not None:
                validator = Draft202012Validator(rule_schema(old))
                if rule["mode"] in ("fixed", "selectable"):
                    values = [rule["value"]] if rule["mode"] == "fixed" else rule["values"]
                    if not all(validator.is_valid(v) for v in values):
                        raise ValueError("Child Tool argument constraint widens its parent")
                elif old["mode"] == "bounded":
                    for bound in ("minimum", "maximum"):
                        if bound in old:
                            if bound not in rule:
                                narrowed[bound] = old[bound]
                            elif (bound == "minimum" and rule[bound] < old[bound]
                                  or bound == "maximum" and rule[bound] > old[bound]):
                                raise ValueError("Child Tool argument constraint widens its parent")
                else:
                    raise ValueError("Child constraint must preserve a fixed/selectable parent")
            result.setdefault(name, {})[key] = narrowed
    validate_tool_constraints(result)
    return result


def constrained_parameters(schema: dict, fields: dict) -> dict:
    """로컬 $defs와 원본 제약을 보존한다. 공개 스키마를 수정하지 않는다."""
    result = deepcopy(schema)
    validator = Draft202012Validator(schema)
    for name, rule in fields.items():
        original = schema.get("properties", {}).get(name)
        if original is None:
            raise ValueError(f"Constraint requires a declared Tool parameter: {name}")
        if rule["mode"] in ("fixed", "selectable"):
            values = [rule["value"]] if rule["mode"] == "fixed" else rule["values"]
            for value in values:
                validator.evolve(schema=original).validate(value)
        # allOf keeps enum, exclusive bounds, $ref, conditional and type constraints intact.
        result["properties"][name] = {"allOf": [deepcopy(original), rule_schema(rule)]}
        # Omission must not escape to a handler/component's unconstrained default.
        # Only fixed owns an explicit replacement value; other modes require input.
        if rule["mode"] != "fixed" and name not in result.get("required", []):
            result.setdefault("required", []).append(name)
    return result


def constrained_arguments(schema: dict, values: dict, fields: dict) -> dict:
    ProjectConfig.validate_settings(values)
    if not isinstance(values, dict):
        raise ValueError("Tool arguments must be an object")
    result = deepcopy(values)
    for key, rule in fields.items():
        if rule["mode"] == "fixed" and key not in result:
            result[key] = deepcopy(rule["value"])
    Draft202012Validator(constrained_parameters(schema, fields)).validate(result)
    return result
