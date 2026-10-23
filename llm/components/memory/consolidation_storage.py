"""Consolidation manifest and inventory; filesystem effects belong to storage."""

from typing import NamedTuple


class ConsolidationManifest:
    SCHEMA_VERSION = 1
    INVALID = "Invalid consolidation manifest"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        PENDING = "pending"
        RECEIPTS = "receipts"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT,
                        Field.PENDING, Field.RECEIPTS))

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, schema_version, project_id, component, pending, receipts):
        return {cls.Field.SCHEMA_VERSION: schema_version, cls.Field.PROJECT_ID: project_id,
                cls.Field.COMPONENT: component, pending: {}, receipts: {}}

    @classmethod
    def validate(cls, value, *, schema_version, project_id, component, pending, receipts, fields):
        if (value.keys() != fields
                or type(value[cls.Field.SCHEMA_VERSION]) is not int or value[cls.Field.SCHEMA_VERSION] != schema_version
                or value[cls.Field.PROJECT_ID] != project_id or value[cls.Field.COMPONENT] != component
                or not isinstance(value[pending], dict) or not isinstance(value[receipts], dict)
                or value[pending].keys() & value[receipts].keys()):
            raise ValueError(cls.INVALID)

    def keys(self, group):
        return self._value[group].keys()

    def items(self, group):
        return self._value[group].items()

    def declare(self, group, identifier, revision):
        self._value[group][identifier] = revision

    def remove(self, group, identifier):
        del self._value[group][identifier]


class ConsolidationInventory(NamedTuple):
    manifest: dict
    pending: dict
    receipts: dict
