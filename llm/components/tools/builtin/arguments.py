"""Builtin Tool input fields and their public Schema share one declaration."""

from copy import deepcopy
from dataclasses import MISSING, fields

from llm.core.schema import SchemaKeyword, object_schema


class BuiltinArguments:
    """Read selected input fields; admission validation stays with ToolRegistry.

    Reading never changes the caller's dictionary or writes effective defaults
    back to a request. Fields owned by another boundary are not interpreted here.
    """

    SCHEMA = "schema"

    @classmethod
    def read(cls, value):
        values = {}
        for item in fields(cls):
            if item.name in value:
                values[item.name] = deepcopy(value[item.name])
            elif item.default is MISSING and item.default_factory is MISSING:
                raise KeyError(item.name)
        return cls(**values)

    @classmethod
    def schema(cls):
        return object_schema(
            {item.name: deepcopy(item.metadata[cls.SCHEMA]) for item in fields(cls)},
            **{SchemaKeyword.REQUIRED: [item.name for item in fields(cls)
                if item.default is MISSING and item.default_factory is MISSING]})
