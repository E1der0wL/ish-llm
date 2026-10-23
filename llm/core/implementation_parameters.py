"""Owned config/policy envelope and explicit path-presence queries."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParameterLookup:
    SEPARATOR = "."
    PRESENT = True
    MISSING = False
    ABSENT_VALUE = None

    present: bool
    value: Any

    @classmethod
    def read(cls, data, path):
        for part in path.split(cls.SEPARATOR):
            if not isinstance(data, dict) or part not in data:
                return cls(cls.MISSING, cls.ABSENT_VALUE)
            data = data[part]
        return cls(cls.PRESENT, data)


@dataclass(frozen=True, init=False, slots=True)
class ImplementationParameters:
    """One envelope Reader; selected implementations own its open nested fields."""

    CONFIG = "config"
    POLICY = "policy"
    SECTIONS = (CONFIG, POLICY)
    DEFAULT_SCOPE = "implementation"
    INVALID_OBJECT = "{scope} configuration must be an object"
    UNKNOWN_SECTIONS = "{scope}: configuration belong under config/policy: {extra}"
    INVALID_SECTION = "{scope}.{key} must be an object; omit an unset section"

    _value: dict

    @classmethod
    def validate(cls, value, *, scope=DEFAULT_SCOPE):
        if not isinstance(value, dict):
            raise TypeError(cls.INVALID_OBJECT.format(scope=scope))
        extra = value.keys() - set(cls.SECTIONS)
        if extra:
            raise ValueError(cls.UNKNOWN_SECTIONS.format(scope=scope, extra=sorted(extra)))
        for key in cls.SECTIONS:
            if key in value and not isinstance(value[key], dict):
                raise TypeError(cls.INVALID_SECTION.format(scope=scope, key=key))

    @classmethod
    def read(cls, value, *, scope=DEFAULT_SCOPE):
        cls.validate(value, scope=scope)
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def config(self):
        return deepcopy(self._value.get(self.CONFIG, {}))

    @property
    def policy(self):
        return deepcopy(self._value.get(self.POLICY, {}))

    def select(self, path):
        selected = ParameterLookup.read(self._value, path)
        return ParameterLookup(selected.present, deepcopy(selected.value))

    def to_dict(self):
        return deepcopy(self._value)
