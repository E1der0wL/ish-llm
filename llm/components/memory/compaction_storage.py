"""Compaction inventory wire contracts; Compactions owns filesystem effects."""

from typing import NamedTuple
from llm.services.infrastructure.storage import revision_token
from .compaction_records import CompactionState


class CompactionManifest:
    SCHEMA_VERSION = 1
    INVALID = "Invalid Memory compaction manifest"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        RECORDS = "records"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT, Field.RECORDS))

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, schema_version, project_id, component):
        return {cls.Field.SCHEMA_VERSION: schema_version, cls.Field.PROJECT_ID: project_id,
                cls.Field.COMPONENT: component, cls.Field.RECORDS: {}}

    @classmethod
    def validate(cls, value, *, schema_version, project_id, component):
        if (value.keys() != cls.FIELDS
                or type(value[cls.Field.SCHEMA_VERSION]) is not int or value[cls.Field.SCHEMA_VERSION] != schema_version
                or value[cls.Field.PROJECT_ID] != project_id or value[cls.Field.COMPONENT] != component
                or not isinstance(value[cls.Field.RECORDS], dict)):
            raise ValueError(cls.INVALID)

    @property
    def keys(self):
        return self._value[self.Field.RECORDS].keys()

    @property
    def items(self):
        return self._value[self.Field.RECORDS].items()

    def declare(self, key, scope, value):
        self._value[self.Field.RECORDS][key] = CompactionInventoryEntry.create(scope, revision_token(value))

    def remove(self, key):
        del self._value[self.Field.RECORDS][key]


class CompactionInventoryEntry:
    SCOPE = CompactionState.Field.SCOPE
    REVISION = CompactionState.Field.REVISION
    FIELDS = frozenset((SCOPE, REVISION))
    INVALID = "Invalid compaction inventory entry"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or value.keys() != cls.FIELDS:
            raise ValueError(cls.INVALID)
        return cls(value)

    @classmethod
    def create(cls, scope, revision):
        return {cls.SCOPE: scope, cls.REVISION: revision}

    @property
    def scope(self):
        return self._value[self.SCOPE]

    @property
    def revision(self):
        return self._value[self.REVISION]


class CompactionInventory(NamedTuple):
    manifest: dict
    records: dict
