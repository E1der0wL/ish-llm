"""Explicit child environment syntax, with no parent merge or string coercion."""

from collections.abc import Mapping
from copy import deepcopy


class ProcessEnvironment:
    SEPARATOR = "="
    NUL = "\0"
    MAPPING_REQUIRED = "env must be a mapping of strings"
    INVALID = "Invalid process environment"
    OWNER = "child process environment"
    DESCRIPTION = "Complete child environment; replaces rather than merges. Omit to use the configured environment; {} passes no variables."
    KEY_SCHEMA = {"type": "string", "minLength": 1, "pattern": "^[^=\\u0000]+$"}
    VALUE_SCHEMA = {"type": "string", "pattern": "^[^\\u0000]*$"}

    @classmethod
    def copy(cls, env: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(env, Mapping):
            raise ValueError(cls.MAPPING_REQUIRED)
        result = dict(env)
        if any(not isinstance(k, str) or not k or not isinstance(v, str)
               or cls.SEPARATOR in k or cls.NUL in k + v for k, v in result.items()):
            raise ValueError(cls.INVALID)
        return result

    @classmethod
    def schema(cls):
        from llm.core.schema import open_schema, SchemaKeyword, OpenSchemaKind
        value = open_schema(cls.OWNER, category=OpenSchemaKind.ADAPTER,
            **{SchemaKeyword.DESCRIPTION: cls.DESCRIPTION,
               SchemaKeyword.PROPERTY_NAMES: deepcopy(cls.KEY_SCHEMA)})
        value[SchemaKeyword.ADDITIONAL_PROPERTIES] = deepcopy(cls.VALUE_SCHEMA)
        return value
