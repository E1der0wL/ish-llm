"""Data definitions and explicit quarantine evidence; no filesystem effects."""

from datetime import datetime

from llm.components.file_manifest import FileManifest
from llm.core.editable_snapshot import EditableSnapshot
from llm.core.schema import SchemaType, SchemaKeyword, object_schema, metadata_schema
from llm.services.infrastructure.storage import revision_token


class DataDefinition:
    TEXT_SCHEMA = {SchemaKeyword.TYPE: SchemaType.STRING}

    class Field:
        TITLE = "title"
        DESCRIPTION = "description"
        METADATA = "metadata"

    @classmethod
    def schema(cls):
        return object_schema({
            cls.Field.TITLE: dict(cls.TEXT_SCHEMA),
            cls.Field.DESCRIPTION: dict(cls.TEXT_SCHEMA),
            cls.Field.METADATA: metadata_schema(),
            FileManifest.FIELD: FileManifest.schema(),
        })


class DataQuarantineInspection:
    class Field:
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        IDENTIFIER = "identifier"
        RECORD_REVISION = "record_revision"
        FILES = "files"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, project_id, component, identifier, record_revision, files):
        return {cls.Field.PROJECT_ID: project_id, cls.Field.COMPONENT: component,
                cls.Field.IDENTIFIER: identifier, cls.Field.RECORD_REVISION: record_revision,
                cls.Field.FILES: files}

    @property
    def files(self):
        return self._value[self.Field.FILES]

    @classmethod
    def snapshot(cls, value):
        return EditableSnapshot(value, revision_token(value)).to_dict()


class DataQuarantineReceipt(DataQuarantineInspection):
    """Receipt format independent of the package definition's FileManifest."""

    SCHEMA_VERSION = 1
    STATUS = "quarantined"
    INVALID = "Invalid Data quarantine receipt"
    INVALID_TIME = "Data quarantine timestamp requires a timezone"

    class Field(DataQuarantineInspection.Field):
        SCHEMA_VERSION = "schema_version"
        STATUS = "status"
        ID = "id"
        CREATED_AT = "created_at"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.STATUS, Field.ID, Field.PROJECT_ID,
                        Field.COMPONENT, Field.IDENTIFIER, Field.RECORD_REVISION,
                        Field.FILES, Field.CREATED_AT))

    @classmethod
    def create(cls, inspection, identifier, at):
        return {**inspection, cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION,
                cls.Field.STATUS: cls.STATUS, cls.Field.ID: identifier, cls.Field.CREATED_AT: at}

    @classmethod
    def validate(cls, value, *, project_id, component, identifier, definition):
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or type(value[cls.Field.SCHEMA_VERSION]) is not int or value[cls.Field.SCHEMA_VERSION] != cls.SCHEMA_VERSION
                or value[cls.Field.STATUS] != cls.STATUS
                or not isinstance(value[cls.Field.ID], str) or not value[cls.Field.ID]
                or value[cls.Field.PROJECT_ID] != project_id or value[cls.Field.COMPONENT] != component
                or value[cls.Field.IDENTIFIER] != identifier
                or value[cls.Field.RECORD_REVISION] != revision_token(definition)
                or not isinstance(value[cls.Field.FILES], dict) or not isinstance(value[cls.Field.CREATED_AT], str)):
            raise ValueError(cls.INVALID)
        if datetime.fromisoformat(value[cls.Field.CREATED_AT]).utcoffset() is None:
            raise ValueError(cls.INVALID_TIME)
