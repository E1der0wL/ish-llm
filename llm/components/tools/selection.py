"""Tool selection configuration; repository existence and lifecycle locks stay with callers."""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Sequence

from jsonschema import Draft202012Validator

from llm.components.base import validate_name
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean, object_schema


@dataclass(frozen=True, init=False, slots=True)
class ToolSelection:
    ABSENT = None
    NO_CHOICES = None
    NO_DESCRIPTION = None
    INVALID_NAMES = "Tool names must be a sequence"
    INVALID_CONFIGURATION = "Invalid {scope} configuration: {message}"

    enabled: tuple[str, ...] | None = field(default=ABSENT, metadata={
        SchemaKeyword.TYPE: SchemaType.ARRAY,
        SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING},
        SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT})

    @classmethod
    def schema(cls, *, choices=NO_CHOICES, description=NO_DESCRIPTION):
        properties = {f.name: deepcopy(dict(f.metadata)) for f in fields(cls)}
        value = properties[cls.enabled.__name__]
        if choices is not cls.NO_CHOICES:
            items = value.pop(SchemaKeyword.ITEMS)
            items[SchemaKeyword.ENUM] = list(choices)
            value[SchemaKeyword.ITEMS] = items
        if description is not cls.NO_DESCRIPTION:
            value[SchemaKeyword.DESCRIPTION] = description
        return object_schema(properties)

    @classmethod
    def read(cls, value, *, scope):
        error = next(Draft202012Validator(cls.schema()).iter_errors(value), cls.ABSENT)
        if error is not cls.ABSENT:
            raise ValueError(cls.INVALID_CONFIGURATION.format(scope=scope, message=error.message))
        result = object.__new__(cls)
        object.__setattr__(result, cls.enabled.__name__,
            tuple(value[cls.enabled.__name__]) if cls.enabled.__name__ in value else cls.ABSENT)
        return result

    @classmethod
    def identifiers(cls, names: Sequence[str]) -> list[str]:
        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
            raise ValueError(cls.INVALID_NAMES)
        return [validate_name(name) for name in names]

    @property
    def configured(self):
        return self.enabled is not self.ABSENT

    @property
    def names(self):
        return list(self.enabled) if self.enabled is not self.ABSENT else []

    def to_dict(self):
        return {type(self).enabled.__name__: self.names} if self.enabled is not self.ABSENT else {}

    @classmethod
    def selected(cls, names, *, scope):
        return cls.read({cls.enabled.__name__: list(names)}, scope=scope)

    def add(self, names, *, scope):
        return self.selected(dict.fromkeys([*self.names, *names]), scope=scope)

    def remove(self, names, *, scope):
        return self.selected((name for name in self.names if name not in names), scope=scope)
