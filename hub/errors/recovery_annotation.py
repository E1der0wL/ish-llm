"""Optional Hub recovery explanation; never classification or authority to retry."""

from dataclasses import dataclass, fields
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class RecoveryAnnotation:
    KEY: ClassVar[str] = "hub_recovery"

    explanation: str
    outcome: str
    attempt: int

    def to_record(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def in_details(self, details):
        return {**details, self.KEY: self.to_record()}

    @classmethod
    def observe(cls, details):
        value = details.get(cls.KEY, {})
        return RecoveryAnnotationObservation(value if isinstance(value, dict) else {})


class RecoveryAnnotationObservation:
    NO_TEXT = ""

    def __init__(self, value):
        self._value = value

    @property
    def explanation(self):
        key = RecoveryAnnotation.explanation.__name__
        return (
            self._value.get(key)
            if isinstance(self._value.get(key), str)
            else self.NO_TEXT
        )

    @property
    def outcome(self):
        key = RecoveryAnnotation.outcome.__name__
        return (
            self._value.get(key)
            if isinstance(self._value.get(key), str)
            else self.NO_TEXT
        )
