"""Detached public edit observations; mutation authority remains with the Owner."""

from copy import deepcopy
from dataclasses import dataclass

from .contracts import JsonValue


@dataclass(frozen=True, slots=True)
class EditableSnapshotFields(JsonValue):
    """Canonical wire fields. Component-specific data and version stay opaque."""

    data: dict
    version: str


@dataclass(frozen=True, slots=True, init=False)
class EditableSnapshot:
    """Read-only observation with detached payload access, never a CAS bypass."""

    _record: EditableSnapshotFields

    def __init__(self, data, version):
        object.__setattr__(self, self.__class__._record.__name__, EditableSnapshotFields(data, version))

    @classmethod
    def read(cls, value):
        record = EditableSnapshotFields.from_dict(value)
        return cls(record.data, record.version)

    @property
    def data(self):
        return deepcopy(self._record.data)

    @property
    def version(self):
        return self._record.version

    def to_dict(self):
        return self._record.to_dict()
