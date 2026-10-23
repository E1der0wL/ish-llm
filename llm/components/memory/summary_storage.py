"""Memory-owned evidence for the presence of each Session summary."""

import re
from typing import NamedTuple

from llm.components.base import validate_name
from llm.services.infrastructure.storage import atomic_json, make_directory, revision_token, unlink_file


class SummaryManifest:
    SCHEMA_VERSION = 1
    REVISION_PATTERN = r"[a-f0-9]{64}"
    NO_MATCH = None
    INVALID_MANIFEST = "Invalid Memory summary manifest"
    INVALID_REVISION = "Invalid Memory summary revision"

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        SUMMARIES = "summaries"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT, Field.SUMMARIES))

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, project_id, component, schema_version):
        return {cls.Field.SCHEMA_VERSION: schema_version, cls.Field.PROJECT_ID: project_id,
                cls.Field.COMPONENT: component, cls.Field.SUMMARIES: {}}

    @classmethod
    def read(cls, value, *, project_id, component, schema_version):
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or type(value[cls.Field.SCHEMA_VERSION]) is not int
                or value[cls.Field.SCHEMA_VERSION] != schema_version
                or value[cls.Field.PROJECT_ID] != project_id
                or value[cls.Field.COMPONENT] != component
                or not isinstance(value[cls.Field.SUMMARIES], dict)):
            raise ValueError(cls.INVALID_MANIFEST)
        return cls(value)

    @property
    def revisions(self):
        return self._value[self.Field.SUMMARIES]

    @classmethod
    def validate_revision(cls, revision):
        if not isinstance(revision, str) or re.fullmatch(cls.REVISION_PATTERN, revision) is cls.NO_MATCH:
            raise ValueError(cls.INVALID_REVISION)

    def record(self, session_id, value):
        self.revisions[session_id] = revision_token(value)

    def remove(self, session_id):
        del self.revisions[session_id]

    def to_record(self):
        return self._value


class SummaryInventory(NamedTuple):
    manifest: dict
    records: dict


class SummaryStorage:
    SCHEMA_VERSION = SummaryManifest.SCHEMA_VERSION
    MANIFEST = "contexts.manifest.json"
    DIRECTORY = "contexts"
    SUFFIX = ".json"
    ENCODING = "utf-8"
    ALREADY_EXISTS = "Cannot adopt existing Memory summary storage"
    INVALID_FILE = "Memory summary evidence must be a regular file"
    INVALID_JSON = "Invalid Memory summary JSON"
    MISSING_DIRECTORY = "Memory summary directory is missing"
    INVALID_ENTRY = "Invalid Memory summary entry"
    MISSING_RECORDS = "Memory summaries are missing or undeclared"
    CHANGED_REVISION = "Memory summary revision changed"
    MISSING_SUMMARY = "Memory summary does not exist"

    def __init__(self, project, component):
        self.project_id, self.component = project.id, component
        self.checked = component._checked
        root = self.checked(component.root(project))
        self.directory = self.checked(root / self.DIRECTORY)
        self.manifest = self.checked(root / self.MANIFEST)

    def path(self, session_id):
        return self.checked(self.directory / (validate_name(session_id) + self.SUFFIX))

    def initialize(self):
        if self.manifest.exists() or self.directory.exists():
            raise ValueError(self.ALREADY_EXISTS)
        make_directory(self.directory)
        atomic_json(self.manifest, SummaryManifest.create(schema_version=self.SCHEMA_VERSION,
            project_id=self.project_id, component=self.component.name))

    def _json(self, path):
        path = self.checked(path)
        if not path.is_file():
            raise ValueError(self.INVALID_FILE)
        try:
            return self.component.deserialize(path.read_text(encoding=self.ENCODING))
        except TypeError as error:
            raise ValueError(self.INVALID_JSON) from error

    def inventory(self):
        manifest = SummaryManifest.read(self._json(self.manifest), project_id=self.project_id,
                                        component=self.component.name, schema_version=self.SCHEMA_VERSION)
        if not self.directory.is_dir():
            raise ValueError(self.MISSING_DIRECTORY)
        paths = {}
        for path in self.directory.iterdir():
            self.checked(path)
            if path.suffix != self.SUFFIX or not path.is_file():
                raise ValueError(self.INVALID_ENTRY)
            paths[validate_name(path.stem)] = path
        if paths.keys() != manifest.revisions.keys():
            raise ValueError(self.MISSING_RECORDS)
        records = {}
        for session_id, revision in manifest.revisions.items():
            SummaryManifest.validate_revision(revision)
            value = self._json(paths[session_id])
            self.component._validate_summary(session_id, value)
            if revision_token(value) != revision:
                raise ValueError(self.CHANGED_REVISION)
            records[session_id] = value
        return SummaryInventory(manifest.to_record(), records)

    def read(self, session_id):
        validate_name(session_id)
        return self.inventory().records.get(session_id)

    def write(self, session_id, value):
        manifest, _ = self.inventory()
        self.component._validate_summary(session_id, value)
        SummaryManifest(manifest).record(session_id, value)
        atomic_json(self.path(session_id), value)
        atomic_json(self.manifest, manifest)

    def clear(self, session_id):
        manifest, records = self.inventory()
        if session_id not in records:
            raise FileNotFoundError(self.MISSING_SUMMARY)
        SummaryManifest(manifest).remove(session_id)
        unlink_file(self.path(session_id))
        atomic_json(self.manifest, manifest)
