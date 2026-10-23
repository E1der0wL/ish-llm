"""Canonical token observations and quota projections, independent of storage."""

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import ClassVar


class TokenField(StrEnum):
    PROMPT = "prompt_tokens"
    COMPLETION = "completion_tokens"
    TOTAL = "total_tokens"


class TokenUsage:
    """Detached normalized observation; unrecognized provider detail is opaque."""

    INVALID_OBJECT = "Model usage must be a normalized dictionary"
    INVALID_COUNT = "Model usage must contain nonnegative token counts"
    UNKNOWN_TOTAL = "Complete usage requires total_tokens"
    MINIMUM_TOKENS = 0

    def __init__(self, value):
        if not isinstance(value, dict):
            raise TypeError(self.INVALID_OBJECT)
        self._value = deepcopy(value)

    @property
    def total_tokens(self):
        value = self._value.get(TokenField.TOTAL)
        return value if type(value) is int and value >= self.MINIMUM_TOKENS else None

    def validate(self, *, complete, invalid_count=INVALID_COUNT, unknown_total=UNKNOWN_TOTAL):
        for key in TokenField:
            value = self._value.get(key)
            if value is not None and (type(value) is not int or value < self.MINIMUM_TOKENS):
                raise ValueError(invalid_count)
        if complete and self.total_tokens is None:
            raise ValueError(unknown_total)

    def to_dict(self):
        return deepcopy(self._value)

    @classmethod
    def aggregate(cls, receipts):
        observations = [cls(receipt.usage) for receipt in receipts]
        result = {}
        for key in TokenField:
            values = [observation._value.get(key) for observation in observations]
            result[key] = (sum(values) if values and all(type(value) is int for value in values)
                           and all(receipt.usage_complete for receipt in receipts) else None)
        return cls(result)


@dataclass(frozen=True, slots=True)
class UsageSource:
    """A currently owning Run, not a grant of execution authority."""

    INVALID_FIELDS: ClassVar[str] = "Component model call requires current source ownership fields"
    project_id: str
    session_id: str
    run_id: str

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID_FIELDS)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UsageReceipt:
    """Quota projection of an already read receipt, never a storage Reader.

    Missing usage remains unknown; only a known total or its original reservation
    can authorize a token-limited call. Period selection requires an original date.
    """

    INVALID_TIME: ClassVar[str] = "Usage period evidence requires a timezone"
    started_at: str | None
    usage: dict
    usage_complete: bool
    reserved_tokens: int | None

    @classmethod
    def from_record(cls, value):
        # Extension projections may omit unknown counters, never assume zero.
        return cls(**{item.name: deepcopy(value.get(item.name)) for item in fields(cls)})

    @classmethod
    def from_result(cls, result):
        return cls(**{item.name: deepcopy(getattr(result, item.name)) for item in fields(cls)})

    @property
    def known_tokens(self):
        return TokenUsage(self.usage).total_tokens if self.usage_complete else None

    @property
    def amount(self):
        known = self.known_tokens
        return known if known is not None else self.reserved_tokens

    def in_period(self, seconds, *, at=None):
        if seconds is None:
            return True
        clock = datetime.now(timezone.utc) if at is None else at
        started = datetime.fromisoformat(self.started_at)
        if started.utcoffset() is None:
            raise ValueError(self.INVALID_TIME)
        return started >= clock - timedelta(seconds=seconds)

    def to_dict(self):
        return asdict(self)


class UsageOrigin(StrEnum):
    RUN = "run"
    COMPONENT = "component"


@dataclass(frozen=True, slots=True)
class UsageCall:
    """Project query projection, preserving each source's public receipt shape."""

    SOURCE: ClassVar[str] = "source"
    SESSION: ClassVar[str] = "session_id"
    RUN: ClassVar[str] = "run_id"
    receipt: UsageReceipt
    _record: dict

    @classmethod
    def for_run(cls, result, run):
        return cls(UsageReceipt.from_result(result), {
            **asdict(result), cls.SOURCE: UsageOrigin.RUN,
            cls.SESSION: run.session_id, cls.RUN: run.id})

    @classmethod
    def for_component(cls, record):
        return cls(UsageReceipt.from_record(record), {**deepcopy(record), cls.SOURCE: UsageOrigin.COMPONENT})

    @classmethod
    def belongs_to_run(cls, record, run_id):
        return run_id is not None and record.get(cls.RUN) == run_id

    def to_dict(self):
        return deepcopy(self._record)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Single Owner of Project usage arithmetic and its existing public response."""

    calls: list
    call_count: int
    known_tokens: int
    reserved_tokens: int
    unknown_calls: int
    period_seconds: float | None

    @classmethod
    def collect(cls, calls, policy, *, at=None):
        clock = datetime.now(timezone.utc) if at is None else at
        calls = [call for call in calls if call.receipt.in_period(policy.period_seconds, at=clock)]
        known = reserved = unknown = 0
        for call in calls:
            receipt = call.receipt
            tokens = receipt.known_tokens
            if tokens is not None:
                known += tokens
            elif receipt.reserved_tokens is not None:
                reserved += receipt.reserved_tokens
            else:
                unknown += 1
        return cls([call.to_dict() for call in calls], len(calls), known, reserved, unknown, policy.period_seconds)

    def to_dict(self):
        return asdict(self)
