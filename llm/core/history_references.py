"""Component-owned history references; collection does not interpret Component records."""

from dataclasses import dataclass

from .configuration import UNSET


@dataclass(frozen=True, slots=True)
class HistoryReferenceRecord:
    session_ids: object = UNSET
    run_ids: object = UNSET
    message_ids: object = UNSET

    @classmethod
    def project(cls, **values):
        """Keep each producer's field presence, order and already selected values."""
        record = cls(**values)
        return {name: getattr(record, name) for name in values}


class HistoryReferenceSet:
    READER_METHOD = "history_references"
    NO_READER = None

    def __init__(self, value):
        # The aggregate's fields are required on access, never filled on read.
        self._value = value

    @classmethod
    def empty(cls):
        return cls(HistoryReferenceRecord.project(session_ids=set(), run_ids=set(), message_ids=set()))

    @classmethod
    def reader(cls, component):
        return getattr(component, cls.READER_METHOD, cls.NO_READER)

    def merge(self, value):
        for kind, values in value.items():
            if kind in self._value:
                self._value[kind].update(values)

    @property
    def session_ids(self):
        return self._value[HistoryReferenceRecord.session_ids.__name__]

    @property
    def run_ids(self):
        return self._value[HistoryReferenceRecord.run_ids.__name__]

    @property
    def message_ids(self):
        return self._value[HistoryReferenceRecord.message_ids.__name__]

    def to_dict(self):
        """Return this call's owned collection, preserving its public mutable sets."""
        return self._value
