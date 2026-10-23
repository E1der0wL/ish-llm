"""Normalized Provider observations; neither SDK wire nor retry authorization."""

from copy import deepcopy
from dataclasses import dataclass, fields
from typing import ClassVar

from llm.core.contracts import ErrorReason


@dataclass(frozen=True, slots=True)
class ProviderRequestFailure:
    KEY: ClassVar[str] = "request_failure"

    reason: str
    cause_index: int
    status_code: int

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def details(self):
        return {self.KEY: self.to_dict()}


@dataclass(frozen=True, slots=True, init=False)
class ProviderRequestHint:
    """Observe a recognized reason while retaining all supplied diagnostic details.

    Incomplete/extended observations are not execution records. Unknown reasons
    remain unclassified, and this projection never repairs or grants a retry.
    """

    RECOGNIZED: ClassVar[tuple] = (ErrorReason.CONTEXT, ErrorReason.PARAMETER)
    UNKNOWN: ClassVar[None] = None

    _value: dict

    @classmethod
    def from_details(cls, details):
        value = details.get(ProviderRequestFailure.KEY)
        if not isinstance(value, dict) or value.get(ProviderRequestFailure.reason.__name__) not in cls.RECOGNIZED:
            return cls.UNKNOWN
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        return result

    @property
    def reason(self):
        return self._value[ProviderRequestFailure.reason.__name__]

    def to_dict(self):
        return deepcopy(self._value)


class ProviderRetryObservation:
    RECOVERY_REJECTED = "completion_recovery_rejected"
    RECOVERY = "completion_recovery"
    STOPPED = "provider_retry_stopped"
    RETRY = "provider_retry"
    RESPONSE_VALIDATION = "response_validation"
    DEADLINE = "deadline"
    PROPOSAL_FAILED = "proposal_failed"
    EXCEEDED_DELAY = "retry_after_exceeds_max_delay"


class ProviderDiagnosticText:
    CODE_SEPARATOR = "_"
    WORD_SEPARATOR = " "

    @classmethod
    def describe(cls, code):
        return code.replace(cls.CODE_SEPARATOR, cls.WORD_SEPARATOR)


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    """One-based observation index; never a configured retry default or limit."""

    ORDINAL_INCREMENT: ClassVar[int] = 1

    call_id: str
    attempt: int

    @classmethod
    def from_index(cls, call_id, index):
        return cls(call_id, index + cls.ORDINAL_INCREMENT)

    @property
    def next_attempt(self):
        return self.attempt + self.ORDINAL_INCREMENT

    def details(self, request_details):
        # Adapter observations keep the original final merge precedence.
        return {self.__class__.call_id.__name__: self.call_id,
                self.__class__.attempt.__name__: self.attempt, **request_details}

    def next_details(self, details):
        return {**details, self.__class__.attempt.__name__: self.next_attempt}
