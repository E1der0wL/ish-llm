"""Small declarative settings codecs shared by their owning records."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, field, fields
from enum import StrEnum
from typing import Any, Self
import math
import unicodedata


class SettingsError(ValueError):
    """A user setting could not be interpreted by its owner."""


class Rule:
    """Own decoding, encoding and the public schema of one value."""

    def decode(self, value: Any) -> Any:
        raise NotImplementedError

    def encode(self, value: Any) -> Any:
        return value

    def schema(self) -> dict:
        raise NotImplementedError

    def documented(self, value):
        """Return a JSON-compatible value or nested public Record for documentation."""
        return self.encode(value)


@dataclass(frozen=True, slots=True)
class Text(Rule):
    nonblank: bool = False
    single_line: bool = False

    def decode(self, value):
        if not isinstance(value, str):
            raise SettingsError("Expected text")
        if self.nonblank and not value.strip():
            raise SettingsError("Text must not be blank")
        if "\x00" in value:
            raise SettingsError("NUL is not allowed")
        if self.single_line and any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in value):
            raise SettingsError("Display text must be a single line without control characters")
        return value

    def schema(self):
        patterns = [r"^[^\u0000]*$"]
        if self.nonblank:
            patterns.append(r"\S")
        if self.single_line:
            patterns.append(r"^[^\u0000-\u001f\u007f-\u009f\u2028\u2029]*$")
        return {"type": "string", "allOf": [{"pattern": p} for p in patterns]}


@dataclass(frozen=True, slots=True)
class Number(Rule):
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False
    exclusive_minimum: bool = False

    def decode(self, value):
        if type(value) not in (int, float) or (self.integer and type(value) is not int):
            raise SettingsError("Expected an integer" if self.integer else "Expected a number")
        if type(value) is float and not math.isfinite(value):
            raise SettingsError("Number must be finite")
        if self.minimum is not None and (
            value <= self.minimum if self.exclusive_minimum else value < self.minimum
        ):
            raise SettingsError(f"Number is below its allowed minimum ({self.minimum})")
        if self.maximum is not None and value > self.maximum:
            raise SettingsError(f"Number exceeds {self.maximum}")
        return value

    def schema(self):
        result = {"type": "integer" if self.integer else "number"}
        if self.minimum is not None:
            result["exclusiveMinimum" if self.exclusive_minimum else "minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        return result


class Boolean(Rule):
    def decode(self, value):
        if type(value) is not bool:
            raise SettingsError("Expected a boolean")
        return value

    def schema(self):
        return {"type": "boolean"}


@dataclass(frozen=True, slots=True)
class Nullable(Rule):
    item: Rule

    def decode(self, value):
        return None if value is None else self.item.decode(value)

    def encode(self, value):
        return None if value is None else self.item.encode(value)

    def schema(self):
        return {"anyOf": [self.item.schema(), {"type": "null"}]}

    def documented(self, value):
        return None if value is None else self.item.documented(value)


@dataclass(frozen=True, slots=True)
class Choice(Rule):
    enum: type[StrEnum]

    def decode(self, value):
        try:
            return self.enum(value)
        except (ValueError, TypeError) as exc:
            raise SettingsError(f"Expected one of: {', '.join(self.enum)}") from exc

    def encode(self, value):
        return value.value

    def schema(self):
        return {"type": "string", "enum": list(self.enum)}


@dataclass(frozen=True, slots=True)
class Sequence(Rule):
    item: Rule
    maximum: int | None = None
    minimum: int = 0

    def decode(self, value):
        if not isinstance(value, (list, tuple)):
            raise SettingsError("Expected an array")
        if len(value) < self.minimum or (self.maximum is not None and len(value) > self.maximum):
            raise SettingsError("Array length is outside its allowed range")
        return tuple(self.item.decode(item) for item in value)

    def encode(self, value):
        return [self.item.encode(item) for item in value]

    def documented(self, value):
        return [self.item.documented(item) for item in value]

    def schema(self):
        result = {"type": "array", "items": self.item.schema(), "minItems": self.minimum}
        if self.maximum is not None:
            result["maxItems"] = self.maximum
        return result


@dataclass(frozen=True, slots=True)
class Nested(Rule):
    owner: type[Record]

    def decode(self, value):
        if isinstance(value, self.owner):
            return value
        return self.owner.from_dict(value)

    def encode(self, value):
        return value.to_dict()

    def schema(self):
        return self.owner.schema()

    def documented(self, value):
        return value


_RULE = "dok.rule"
_DESCRIPTION = "dok.description"


def setting(rule: Rule, *, default=MISSING, factory=MISSING, description=""):
    """Declare one field once for construction, JSON I/O and schema generation."""
    return field(default=default, default_factory=factory, metadata={_RULE: rule, _DESCRIPTION: description})


@dataclass(frozen=True, slots=True)
class DocumentedField:
    name: str
    value: Any
    description: str


class Record:
    """Dataclass field names and rules are the only serialized field declaration."""

    def __post_init__(self) -> None:
        for item in fields(self):
            try:
                value = item.metadata[_RULE].decode(getattr(self, item.name))
            except SettingsError as exc:
                raise SettingsError(f"{type(self).__name__}.{item.name}: {exc}") from exc
            object.__setattr__(self, item.name, value)
        self._check()

    def _check(self) -> None:
        pass

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        if not isinstance(value, dict):
            raise SettingsError(f"{cls.__name__}: expected an object")
        names = {item.name for item in fields(cls)}
        if unknown := value.keys() - names:
            raise SettingsError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
        try:
            return cls(**value)
        except TypeError as exc:
            raise SettingsError(f"{cls.__name__}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {item.name: item.metadata[_RULE].encode(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def description(cls, name: str) -> str:
        # A subclass changing only a default inherits the original owner's explanation.
        for owner in cls.__mro__:
            item = vars(owner).get("__dataclass_fields__", {}).get(name)
            if item is not None and (description := item.metadata.get(_DESCRIPTION)):
                return description
        return ""

    def documented_fields(self) -> tuple[DocumentedField, ...]:
        return tuple(DocumentedField(item.name, item.metadata[_RULE].documented(getattr(self, item.name)),
                                     self.description(item.name)) for item in fields(self))

    @classmethod
    def schema(cls) -> dict[str, Any]:
        properties, required = {}, []
        for item in fields(cls):
            rule = item.metadata[_RULE]
            spec = rule.schema()
            if description := cls.description(item.name):
                spec["description"] = description
            if item.default is not MISSING:
                spec["default"] = rule.encode(rule.decode(item.default))
            elif item.default_factory is not MISSING:
                spec["default"] = rule.encode(rule.decode(item.default_factory()))
            else:
                required.append(item.name)
            properties[item.name] = spec
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    @classmethod
    def names(cls):
        """Field references for an owner's cross-field schema constraints."""
        from types import SimpleNamespace

        return SimpleNamespace(**{item.name: item.name for item in fields(cls)})
