"""Canonical inventory evidence for Component-owned JSON definitions."""

import re
import json
from llm.core.json_data import JsonObject
from .naming import ComponentName


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
    SCHEMA_VERSION = 1
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID = "Invalid Component record manifest"
    INVALID_DIGEST = "Invalid Component record digest"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        RECORDS = "records"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT, Field.RECORDS))

    def __init__(self, value):
        self._value = value

    @classmethod
    def empty(cls, schema_version, project_id, component):
        return {
            cls.Field.SCHEMA_VERSION: schema_version,
            cls.Field.PROJECT_ID: project_id,
            cls.Field.COMPONENT: component,
            cls.Field.RECORDS: {},
        }

    @classmethod
    def validate(cls, manifest, schema_version, project_id, component):
        if (
            not isinstance(manifest, dict)
            or manifest.keys() != cls.FIELDS
            or type(manifest[cls.Field.SCHEMA_VERSION]) is not int
            or manifest[cls.Field.SCHEMA_VERSION] != schema_version
            or manifest[cls.Field.PROJECT_ID] != project_id
            or manifest[cls.Field.COMPONENT] != component
            or not isinstance(manifest[cls.Field.RECORDS], dict)
        ):
            raise ValueError(cls.INVALID)
        for identifier, digest in manifest[cls.Field.RECORDS].items():
            ComponentName.validate(identifier)
            if not isinstance(digest, str) or not re.fullmatch(cls.DIGEST_PATTERN, digest):
                raise ValueError(cls.INVALID_DIGEST)

    @property
    def records(self):
        return self._value[self.Field.RECORDS]

    def record(self, identifier, digest):
        self._value[self.Field.RECORDS][identifier] = digest

    def remove(self, identifier):
        del self._value[self.Field.RECORDS][identifier]
