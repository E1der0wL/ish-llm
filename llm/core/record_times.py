"""Owner-managed record times; not revision, authority or validation evidence."""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from typing import ClassVar

from .json_data import JsonValue


@dataclass(frozen=True, slots=True)
class RecordTimes:
    INVALID_TIMESTAMP = 'Record times require explicit UTC timestamps'
    MISSING_TIMES = 'Record times are missing or invalid; re-register unsupported records'
    INVALID_RECORD = 'Record times require a record object'
    IMMUTABLE_CREATION = 'Record creation time is immutable'

    CREATED_AT: ClassVar[str] = "created_at"
    UPDATED_AT: ClassVar[str] = "updated_at"
    created_at: str
    updated_at: str

    def __post_init__(self):
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, str):
                raise ValueError(RecordTimes.INVALID_TIMESTAMP)
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(RecordTimes.INVALID_TIMESTAMP) from error
            if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
                raise ValueError(RecordTimes.INVALID_TIMESTAMP)

    @classmethod
    def create(cls):
        instant = datetime.now(timezone.utc).isoformat()
        return cls(instant, instant)

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != {field.name for field in fields(cls)}:
            raise ValueError(RecordTimes.MISSING_TIMES)
        return cls(**value)

    @classmethod
    def from_record(cls, value):
        """Only an owner whose record declares these fields may select this reader."""
        if not isinstance(value, dict):
            raise ValueError(RecordTimes.INVALID_RECORD)
        return cls.read({field.name: value[field.name] for field in fields(cls) if field.name in value})

    def touch(self):
        return type(self)(self.created_at, datetime.now(timezone.utc).isoformat())

    @classmethod
    def for_update(cls, previous, current):
        """Compare owner-supplied record contents; timestamps are not user edits."""
        times = cls.from_record(previous)
        cls.from_record(current)
        if current[cls.CREATED_AT] != times.created_at:
            raise ValueError(RecordTimes.IMMUTABLE_CREATION)
        content = lambda record: {key: value for key, value in record.items()
                                  if key not in (cls.CREATED_AT, cls.UPDATED_AT)}
        return times if JsonValue.same(content(previous), content(current)) else times.touch()

    def to_dict(self):
        return asdict(self)
