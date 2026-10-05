"""Host-owned Tool 인자 제약. 원본 JSON Schema와 교집합을 만들며 값을 추천하지 않는다."""

from copy import deepcopy
import math
from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.interactions import same_interaction_value


def validate_constraints(constraints: dict) -> None:
    ProjectConfig.validate_settings(constraints)
    if not isinstance(constraints, dict):
        raise ValueError("argument_constraints must be an object")
    for name, fields in constraints.items():
        if not isinstance(name, str) or not name or not isinstance(fields, dict):
            raise ValueError("Tool constraints require named argument objects")
        for key, rule in fields.items():
            if not isinstance(key, str) or not key or not isinstance(rule, dict):
                raise ValueError("Invalid Tool argument constraint")
            mode = rule.get("mode")
            if mode == "fixed":
                valid = rule.keys() == {"mode", "value"}
            elif mode == "selectable":
                values = rule.get("values")
                valid = rule.keys() == {"mode", "values"} and isinstance(values, list) and bool(values)
                if valid:
                    valid = all(not any(same_interaction_value(v, other) for other in values[:i])
                                for i, v in enumerate(values))
            elif mode == "bounded":
                bounds = {k: v for k, v in rule.items() if k != "mode"}
                valid = bool(bounds) and not bounds.keys() - {"minimum", "maximum"} and all(
                    type(v) in (int, float) and math.isfinite(v) for v in bounds.values())
                if valid and bounds.keys() == {"minimum", "maximum"}:
                    valid = bounds["minimum"] <= bounds["maximum"]
            else:
                valid = False
            if not valid:
                raise ValueError(f"Invalid argument constraint: {name}.{key}")


def rule_schema(rule: dict) -> dict:
    if rule["mode"] == "fixed":
        return {"const": deepcopy(rule["value"])}
    if rule["mode"] == "selectable":
        return {"enum": deepcopy(rule["values"])}
    return {"type": "number", **{k: v for k, v in rule.items() if k != "mode"}}


def narrow_constraints(parent: dict, child: dict) -> dict:
    """생략은 상속이다. 허용 범위를 넓히거나 증명할 수 없는 교차 모드는 거부한다."""
    validate_constraints(child)
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
    validate_constraints(result)
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
