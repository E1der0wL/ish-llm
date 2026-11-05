"""Canonical inventory evidence for Component-owned JSON definitions."""

import re
import json
from llm.core.json_data import JsonObject
from .naming import ComponentName
from llm.core.record_times import RecordTimes


class ComponentRecordEncoding:
    ASCII_ONLY = False
    ALLOW_NONFINITE = False
    TEXT_ENCODING = "utf-8"

    @classmethod
    def encode(cls, data):
        JsonObject.validate(data)
        return json.dumps(data, ensure_ascii=cls.ASCII_ONLY, allow_nan=cls.ALLOW_NONFINITE)

    @classmethod
    def decode(cls, value):
        data = json.loads(value)
        JsonObject.validate(data)
        return data


class ComponentRecordManifest:
    MISSING_TIMES = 'Component record times are missing; re-register unsupported records'
    INVALID_TIMES = 'Component record times do not match their owner/inventory'

    SCHEMA_VERSION = 1
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID = "Invalid Component record manifest"
    INVALID_DIGEST = "Invalid Component record digest"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        RECORDS = "records"
        TIMES = "times"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT, Field.RECORDS, Field.TIMES))

    def __init__(self, value):
        self._value = value

    @classmethod
    def empty(cls, schema_version, project_id, component, *, native_times):
        value = {
            cls.Field.SCHEMA_VERSION: schema_version,
            cls.Field.PROJECT_ID: project_id,
            cls.Field.COMPONENT: component,
            cls.Field.RECORDS: {},
        }
        if not native_times:
            value[cls.Field.TIMES] = {}
        return value

    @classmethod
    def validate(cls, manifest, schema_version, project_id, component, *, native_times):
        if isinstance(manifest, dict) and not native_times and cls.Field.TIMES not in manifest:
            raise ValueError(ComponentRecordManifest.MISSING_TIMES)
        expected_fields = cls.FIELDS - {cls.Field.TIMES} if native_times else cls.FIELDS
        if (
            not isinstance(manifest, dict)
            or manifest.keys() != expected_fields
            or type(manifest[cls.Field.SCHEMA_VERSION]) is not int
            or manifest[cls.Field.SCHEMA_VERSION] != schema_version
            or manifest[cls.Field.PROJECT_ID] != project_id
            or manifest[cls.Field.COMPONENT] != component
            or not isinstance(manifest[cls.Field.RECORDS], dict)
            or not native_times and not isinstance(manifest[cls.Field.TIMES], dict)
        ):
            raise ValueError(cls.INVALID)
        for identifier, digest in manifest[cls.Field.RECORDS].items():
            ComponentName.validate(identifier)
            if not isinstance(digest, str) or not re.fullmatch(cls.DIGEST_PATTERN, digest):
                raise ValueError(cls.INVALID_DIGEST)
        if native_times:
            return  # The declared record reader owns the only authoritative times.
        if manifest[cls.Field.TIMES].keys() != manifest[cls.Field.RECORDS].keys():
            raise ValueError(ComponentRecordManifest.INVALID_TIMES)
        for value in manifest[cls.Field.TIMES].values():
            RecordTimes.read(value)

    @property
    def records(self):
        return self._value[self.Field.RECORDS]

    @property
    def times(self):
        return self._value[self.Field.TIMES]

    def record(self, identifier, digest):
        self._value[self.Field.RECORDS][identifier] = digest

    def remove(self, identifier):
        del self._value[self.Field.RECORDS][identifier]
        if self.Field.TIMES in self._value:
            del self.times[identifier]
