"""Workflow JSON pointer and declared input/output/schema binding Owners."""

from copy import deepcopy
from enum import StrEnum
from jsonschema import Draft202012Validator
from llm.core.json_data import JsonPointer


class BindingDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    RESUME = "resume"


class BindingPointer:
    ZERO_INDEX = "0"
    MISSING_PATH = "Missing binding path: {path}"

    @classmethod
    def read(cls, document, path: str):
        value = document
        for part in pointer_parts(path):
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif (
                isinstance(value, list)
                and part.isascii()
                and part.isdecimal()
                and (part == cls.ZERO_INDEX or not part.startswith(cls.ZERO_INDEX))
                and int(part) < len(value)
            ):
                value = value[int(part)]
            else:
                raise KeyError(cls.MISSING_PATH.format(path=path))
        return value


class WorkflowBindings:
    INPUTS = "inputs"
    OUTPUTS = "outputs"
    SCHEMA_SUFFIX = "_schema"
    SCHEMA_NAME = "{direction}" + SCHEMA_SUFFIX
    INPUT_SCHEMA = BindingDirection.INPUT + SCHEMA_SUFFIX
    OUTPUT_SCHEMA = BindingDirection.OUTPUT + SCHEMA_SUFFIX
    RESUME_SCHEMA = BindingDirection.RESUME + SCHEMA_SUFFIX
    MAPPINGS = (INPUTS, OUTPUTS)
    SCHEMAS = (INPUT_SCHEMA, OUTPUT_SCHEMA, RESUME_SCHEMA)
    STATE_FIELDS = (INPUTS, OUTPUTS, INPUT_SCHEMA, OUTPUT_SCHEMA)
    ABSENT = None
    INVALID_MAPPING = "{direction} must be an object"
    INVALID_NAME = "Binding names must be nonempty strings"

    def __init__(self, value):
        self._value = value

    @property
    def has_inputs(self):
        return self.INPUTS in self._value

    @property
    def inputs(self):
        return self._value[self.INPUTS]

    @property
    def has_outputs(self):
        return self.OUTPUTS in self._value

    @property
    def outputs(self):
        return self._value[self.OUTPUTS]

    @property
    def resume_schema(self):
        return self._value.get(self.RESUME_SCHEMA)

    @classmethod
    def validate_resume_state(cls, schema, state):
        validate_value({cls.RESUME_SCHEMA: schema}, BindingDirection.RESUME, state)

    @classmethod
    def validate_contract(cls, definition: dict) -> None:
        for direction in cls.MAPPINGS:
            if direction not in definition:
                continue
            mapping = definition[direction]
            if not isinstance(mapping, dict):
                raise ValueError(cls.INVALID_MAPPING.format(direction=direction))
            for name, path in mapping.items():
                if not isinstance(name, str) or not name:
                    raise ValueError(cls.INVALID_NAME)
                pointer_parts(path)
        from llm.core.schema import validate_local_refs

        for name in cls.SCHEMAS:
            if name in definition:
                validate_local_refs(definition[name])
                Draft202012Validator.check_schema(definition[name])

    @staticmethod
    def bind(mapping: dict, source: dict) -> dict:
        return {name: deepcopy(read_pointer(source, path)) for name, path in mapping.items()}

    @classmethod
    def validate_value(cls, definition: dict, direction: str, value: dict) -> None:
        schema = definition.get(cls.SCHEMA_NAME.format(direction=direction))
        if schema is not cls.ABSENT:
            Draft202012Validator(schema).validate(value)


# Existing public entry points share one implementation with their declared Owners.
pointer_parts = JsonPointer.parse
read_pointer = BindingPointer.read
validate_contract = WorkflowBindings.validate_contract
bind = WorkflowBindings.bind
validate_value = WorkflowBindings.validate_value
