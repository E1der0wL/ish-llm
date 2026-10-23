"""Live reference observations supplied by definition Owners, never a second index."""

from copy import deepcopy
from dataclasses import MISSING, dataclass, fields
from typing import ClassVar

from .contracts import JsonValue
from .json_data import JsonObject


class AgentReferenceReader:
    """Discover an Owner's reference hook; each caller retains its argument/result contract."""

    METHOD = "agent_references"

    @classmethod
    def from_owner(cls, owner):
        return getattr(owner, cls.METHOD, None)


@dataclass(frozen=True, slots=True)
class ResourceTarget(JsonValue):
    WHOLE_COMPONENT: ClassVar[None] = None
    INVALID_TARGET: ClassVar[str] = "Resource references require nonempty Component and record identifiers"
    INVALID_ENGINE: ClassVar[str] = "Engine Agent references require Component and record identifiers"

    component: str
    identifier: str | None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.component or (self.identifier is not self.WHOLE_COMPONENT and not self.identifier):
            raise ValueError(self.INVALID_TARGET)

    @classmethod
    def from_engine(cls, value):
        try:
            result = cls.from_dict(value)
        except (TypeError, ValueError) as error:
            raise ValueError(cls.INVALID_ENGINE) from error
        if result.identifier is cls.WHOLE_COMPONENT:
            raise ValueError(cls.INVALID_ENGINE)
        return result


@dataclass(frozen=True, slots=True)
class ReferenceFields(ResourceTarget):
    NO_PATH: ClassVar[None] = None
    INVALID_SOURCE: ClassVar[str] = "Resource references require a nonempty source identifier"

    source: str
    path: str | None = NO_PATH

    def __post_init__(self):
        ResourceTarget.__post_init__(self)
        if not self.source:
            raise ValueError(self.INVALID_SOURCE)


@dataclass(frozen=True, slots=True, init=False)
class StoredResourceReference:
    """Common fields are typed; optional/extension evidence stays Owner-specific."""

    _value: dict
    _fields: ReferenceFields

    @classmethod
    def read(cls, value):
        JsonObject.validate(value)
        declared = ReferenceFields.from_dict({item.name: value[item.name]
            for item in fields(ReferenceFields) if item.name in value})
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        object.__setattr__(result, cls._fields.__name__, declared)
        return result

    @classmethod
    def from_target(cls, target, source):
        value = ReferenceFields(target.component, target.identifier, source)
        return cls.read({item.name: getattr(value, item.name)
                         for item in fields(ReferenceFields) if item.default is MISSING})

    @classmethod
    def from_value(cls, value, source):
        JsonObject.validate(value)
        return cls.read({**value, ReferenceFields.source.__name__: source})

    @property
    def component(self):
        return self._fields.component

    @property
    def identifier(self):
        return self._fields.identifier

    @property
    def source(self):
        return self._fields.source

    def matches(self, component, identifier=ResourceTarget.WHOLE_COMPONENT, *, path=ReferenceFields.NO_PATH):
        return (self.component == component
                and (identifier is ResourceTarget.WHOLE_COMPONENT or self.identifier == identifier)
                and (path is ReferenceFields.NO_PATH or self._fields.path in (ReferenceFields.NO_PATH, path)))

    def dependent(self, owner):
        return ResourceTarget(owner, self.source)

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True)
class AgentUse(JsonValue):
    """A Workflow's retained Agent selection, reported by the action Owner."""

    INVALID_REFERENCE: ClassVar[str] = "Agent use requires nonempty target and source identifiers"

    identifier: str
    source: str

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.identifier or not self.source:
            raise ValueError(self.INVALID_REFERENCE)
