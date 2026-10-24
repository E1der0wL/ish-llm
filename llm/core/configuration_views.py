"""Typed explicit-configuration observations; these values never become stored defaults."""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import Any

from .contracts import JsonValue
from llm.core.json_data import JsonObject
from llm.core.schema import SchemaExtension


@dataclass(frozen=True, slots=True)
class ConfigurationOverride(JsonValue):
    source: str
    value: Any


@dataclass(frozen=True, slots=True)
class ConfigurationResolution(JsonValue):
    """Optional observations from an extension; creation supplies all four maps."""

    values: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    overridden: dict[str, tuple[ConfigurationOverride, ...]] = field(default_factory=dict)
    editable: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EngineConfigurationKey(JsonValue):
    INVALID_SCHEMA_KEY = "Engine configuration key must be nonempty text"

    configuration_key: str | None

    @classmethod
    def from_view(cls, value, *, default):
        return cls(value.get(cls.configuration_key.__name__, default))

    @classmethod
    def schema_key(cls, schema, *, default):
        key = schema.get(SchemaExtension.PARAMETER_KEY, default) if isinstance(schema, dict) else default
        if not isinstance(key, str) or not key.strip():
            raise ValueError(cls.INVALID_SCHEMA_KEY)
        return key

    @staticmethod
    def schema_fields(key):
        return {SchemaExtension.PARAMETER_KEY: key} if key else {}


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration(JsonValue):
    runtime: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeOnlyConfiguration(JsonValue):
    runtime_only: bool = True


@dataclass(frozen=True, init=False, slots=True)
class ConfigurationView:
    """Detached view of explicit settings. Missing observations stay omitted on wire.

    An extension can omit observations it does not expose. Empty query projections
    never populate Project/Session settings, reconstruct storage, or authorize work.
    Extension details remain opaque to the common configuration layer.
    """

    RESOLVE_METHOD = "resolve_config"
    NO_RESOLVER = None

    _value: dict
    _resolution: ConfigurationResolution
    _runtime: RuntimeConfiguration

    @classmethod
    def resolver(cls, implementation):
        return getattr(implementation, cls.RESOLVE_METHOD, cls.NO_RESOLVER)

    @classmethod
    def read(cls, value):
        JsonObject.validate(value)
        resolution = ConfigurationResolution.from_dict({item.name: value[item.name]
            for item in fields(ConfigurationResolution) if item.name in value})
        runtime = RuntimeConfiguration.from_dict({item.name: value[item.name]
            for item in fields(RuntimeConfiguration) if item.name in value})
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        object.__setattr__(result, cls._resolution.__name__, resolution)
        object.__setattr__(result, cls._runtime.__name__, runtime)
        return result

    @classmethod
    def create(cls, values, sources, overridden, editable):
        return cls.read(ConfigurationResolution(values, sources, overridden, editable).to_dict())

    @classmethod
    def unavailable(cls):
        return cls.read(RuntimeOnlyConfiguration().to_dict())

    @property
    def values(self):
        return deepcopy(self._resolution.values)

    @property
    def sources(self):
        return deepcopy(self._resolution.sources)

    @property
    def overridden(self):
        return deepcopy(self._resolution.overridden)

    @property
    def editable(self):
        return deepcopy(self._resolution.editable)

    @property
    def runtime(self):
        return self._runtime.runtime

    def with_engine_key(self, name):
        return type(self).read({**self.to_dict(), **EngineConfigurationKey(name).to_dict()})

    def with_runtime(self, names):
        return type(self).read({**self.to_dict(), **RuntimeConfiguration(tuple(names)).to_dict()})

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True)
class ConfigurationEnforcement:
    """Implementation-owned enforced observations; never write stored configuration."""

    enforced: dict

    @classmethod
    def apply(cls, view, value):
        view[cls.enforced.__name__] = value
