"""Trusted Tool execution declarations, independent of registration and execution."""

from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Optional


@dataclass(frozen=True, slots=True)
class ToolClassification:
    """Classification supplied by the trusted Application, never inferred from arguments."""

    INVALID_CATEGORY = "Tool classification category must be nonempty"
    REQUIRED_FIELDS = "Stored Tool classification requires exactly the current fields"

    category: str = "tool.execute"
    risk_scheme: Optional[str] = None
    risk: Optional[int] = None

    def __post_init__(self):
        from .interactions import validate_risk
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError(self.INVALID_CATEGORY)
        validate_risk(self.risk_scheme, self.risk)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        """A persisted classification is complete evidence, not a new declaration."""
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.REQUIRED_FIELDS)
        return cls(**value)


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class ToolIsolation(StrEnum):
    NONE = "none"
    PROCESS = "process"
    SANDBOX = "sandbox"


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Developer authority; model descriptions and Project records cannot replace it."""

    REVISION_REQUIRED = "Tool contract revision is required"
    INVALID_REQUIREMENTS = "Tool contract requirements must be boolean"
    INVALID_RETRY_FIELDS = "Tool retry_fields must be distinct parameter names"
    REQUIRED_FIELDS = "Tool contract requires exactly the current fields"
    INVALID_STORED_RETRY_FIELDS = "Stored Tool retry_fields must be an array"

    revision: str
    effect: ToolEffect
    isolation: ToolIsolation
    approval_required: bool
    operation_key_required: bool
    retry_fields: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError(self.REVISION_REQUIRED)
        object.__setattr__(self, ToolContract.effect.__name__, ToolEffect(self.effect))
        object.__setattr__(self, ToolContract.isolation.__name__, ToolIsolation(self.isolation))
        if type(self.approval_required) is not bool or type(self.operation_key_required) is not bool:
            raise TypeError(self.INVALID_REQUIREMENTS)
        if (not isinstance(self.retry_fields, tuple) or any(not isinstance(k, str) or not k for k in self.retry_fields)
                or len(set(self.retry_fields)) != len(self.retry_fields)):
            raise ValueError(self.INVALID_RETRY_FIELDS)

    def to_dict(self):
        return {**asdict(self), ToolContract.effect.__name__: self.effect.value,
                ToolContract.isolation.__name__: self.isolation.value,
                ToolContract.retry_fields.__name__: list(self.retry_fields)}

    @classmethod
    def from_dict(cls, value):
        """Read only a complete current execution declaration."""
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.REQUIRED_FIELDS)
        if not isinstance(value[cls.retry_fields.__name__], list):
            raise TypeError(cls.INVALID_STORED_RETRY_FIELDS)
        return cls(**{**value, cls.retry_fields.__name__: tuple(value[cls.retry_fields.__name__])})
