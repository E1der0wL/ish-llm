"""Memory record definition and state; persistence remains with the Component."""

from datetime import datetime, timezone
import re

from jsonschema import Draft202012Validator
from llm.components.base import validate_name
from llm.core.schema import SchemaType, SchemaKeyword, object_schema, metadata_schema, field


class MemoryRecord:
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    STATUSES = (CANDIDATE, CONFIRMED)
    ALL = "all"
    PROJECT_SCOPE = "project"
    SESSION_SCOPE = "session"
    SCOPES = (PROJECT_SCOPE, SESSION_SCOPE)
    GENERATION_PATTERN = r"[a-f0-9]{32}"
    SCHEMA_GENERATION_PATTERN = r"^[a-f0-9]{32}$"
    MINIMUM_REVISION = 1
    MINIMUM_TEXT_LENGTH = 1
    REVISION_INCREMENT = 1
    ABSENT = None
    INITIAL_KIND = "note"
    INITIAL_DELETED = False
    SCOPE_CHANGED = "Memory scope cannot be changed"
    DELETED_RECORD = "Memory is deleted"
    LIVE_RECORD = "Memory is not deleted"
    MANAGED_CONTENT = "Memory lifecycle fields cannot be supplied as content"
    INVALID_CONTENT = "Memory content: {message}"
    INVALID_STATUS = "Invalid memory status"
    INVALID_GENERATION = "Memory requires a stored generation; re-register records without one"
    INVALID_IDENTITY = "Invalid memory identity or revision"
    NONEMPTY_TEXT = "Memory {key} must be nonempty text"
    TIMEZONE_REQUIRED = "Memory {key} requires a timezone"
    INVALID_SCOPE = "Memory requires project/session scope and a deletion marker"
    PROJECT_SESSION = "Project memories cannot have a session_id"
    INVALID_EXPIRY = "Memory expiry must be ISO datetime text"
    EXPIRY_TIMEZONE = "Memory expiry requires a timezone"
    INVALID_METADATA = "Memory metadata must be an object"
    INVALID_SOURCE_TAGS = "Memory requires source object and text tags"
    INVALID_EXPECTED_REVISION = "expected_revision must be a positive integer"
    INVALID_EXPECTED_GENERATION = "expected_generation must be the generation read from the record"
    CHANGED = "Memory changed; reload before editing"

    class Field:
        ID = "id"
        GENERATION = "generation"
        REVISION = "revision"
        CREATED_AT = "created_at"
        UPDATED_AT = "updated_at"
        DELETED = "deleted"
        SOURCE = "source"
        CONTENT = "content"
        KIND = "kind"
        SCOPE = "scope"
        SESSION_ID = "session_id"
        STATUS = "status"
        TAGS = "tags"
        EXPIRES_AT = "expires_at"
        METADATA = "metadata"
        REPLACES = "replaces"

    MANAGED = frozenset({Field.ID, Field.GENERATION, Field.REVISION, Field.CREATED_AT,
                         Field.UPDATED_AT, Field.DELETED, Field.SOURCE})
    TEXT_FIELDS = (Field.CONTENT, Field.KIND, Field.CREATED_AT, Field.UPDATED_AT)
    TIME_FIELDS = (Field.CREATED_AT, Field.UPDATED_AT)
    SCOPE_FIELDS = (Field.SCOPE, Field.SESSION_ID)
    IMMUTABLE = (Field.ID, Field.GENERATION, Field.CREATED_AT, Field.SOURCE, Field.SCOPE, Field.SESSION_ID)

    def __init__(self, value):
        self._value = value

    @classmethod
    def content_schema(cls, *, scopes=SCOPES, statuses=STATUSES):
        text = {SchemaKeyword.MIN_LENGTH: cls.MINIMUM_TEXT_LENGTH}
        return object_schema({
            cls.Field.CONTENT: field(SchemaType.STRING, **text),
            cls.Field.KIND: field(SchemaType.STRING, **text),
            cls.Field.SCOPE: field(SchemaType.STRING, **{SchemaKeyword.ENUM: list(scopes)}),
            cls.Field.SESSION_ID: field([SchemaType.STRING, SchemaType.NULL]),
            cls.Field.STATUS: field(SchemaType.STRING, **{SchemaKeyword.ENUM: list(statuses)}),
            cls.Field.TAGS: field(SchemaType.ARRAY, **{SchemaKeyword.ITEMS: field(SchemaType.STRING, **text)}),
            cls.Field.EXPIRES_AT: field([SchemaType.STRING, SchemaType.NULL]),
            cls.Field.METADATA: metadata_schema(),
            cls.Field.REPLACES: field(SchemaType.ARRAY, **{SchemaKeyword.ITEMS: object_schema({
                cls.Field.ID: field(SchemaType.STRING, **text),
                cls.Field.GENERATION: field(SchemaType.STRING, **{SchemaKeyword.PATTERN: cls.SCHEMA_GENERATION_PATTERN}),
                cls.Field.REVISION: field(SchemaType.INTEGER, **{SchemaKeyword.MINIMUM: cls.MINIMUM_REVISION})},
                **{SchemaKeyword.REQUIRED: [cls.Field.ID, cls.Field.GENERATION, cls.Field.REVISION]})})})

    @classmethod
    def validate_content(cls, value, *, managed, schema):
        if managed.intersection(value):
            raise ValueError(cls.MANAGED_CONTENT)
        error = next(Draft202012Validator(schema).iter_errors(value), cls.ABSENT)
        if error:
            raise ValueError(cls.INVALID_CONTENT.format(message=error.message)) from error

    def content_fields(self, managed):
        return {key: value for key, value in self._value.items() if key not in managed}

    @classmethod
    def validate_status(cls, status, *, allow_all, statuses, all_status):
        if status not in ((*statuses, all_status) if allow_all else statuses):
            raise ValueError(cls.INVALID_STATUS)

    @classmethod
    def check_revision(cls, record, expected_revision, expected_generation, *, conflict):
        if type(expected_revision) is not int or expected_revision < cls.MINIMUM_REVISION:
            raise ValueError(cls.INVALID_EXPECTED_REVISION)
        if not isinstance(expected_generation, str) or not re.fullmatch(cls.GENERATION_PATTERN, expected_generation):
            raise ValueError(cls.INVALID_EXPECTED_GENERATION)
        if (record[cls.Field.GENERATION], record[cls.Field.REVISION]) != (expected_generation, expected_revision):
            raise conflict(cls.CHANGED)

    def visible(self, session_id, *, project_scope):
        return self._value[self.Field.SCOPE] == project_scope or self._value.get(self.Field.SESSION_ID) == session_id

    def expired(self):
        return bool(self._value.get(self.Field.EXPIRES_AT) and
                    datetime.fromisoformat(self._value[self.Field.EXPIRES_AT]) <= datetime.now(timezone.utc))

    @classmethod
    def validate_fields(cls, identifier, record, *, validate_status, scopes, session_scope):
        if not isinstance(record.get(cls.Field.GENERATION), str) or not re.fullmatch(cls.GENERATION_PATTERN, record[cls.Field.GENERATION]):
            raise ValueError(cls.INVALID_GENERATION)
        if record.get(cls.Field.ID) != identifier or type(record.get(cls.Field.REVISION)) is not int or record[cls.Field.REVISION] < cls.MINIMUM_REVISION:
            raise ValueError(cls.INVALID_IDENTITY)
        for key in cls.TEXT_FIELDS:
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(cls.NONEMPTY_TEXT.format(key=key))
        for key in cls.TIME_FIELDS:
            if datetime.fromisoformat(record[key]).utcoffset() is cls.ABSENT:
                raise ValueError(cls.TIMEZONE_REQUIRED.format(key=key))
        validate_status(record.get(cls.Field.STATUS))
        if record.get(cls.Field.SCOPE) not in scopes or type(record.get(cls.Field.DELETED)) is not bool:
            raise ValueError(cls.INVALID_SCOPE)
        if record[cls.Field.SCOPE] == session_scope:
            validate_name(record.get(cls.Field.SESSION_ID))
        elif record.get(cls.Field.SESSION_ID) is not cls.ABSENT:
            raise ValueError(cls.PROJECT_SESSION)
        if record.get(cls.Field.EXPIRES_AT) is not cls.ABSENT:
            if not isinstance(record[cls.Field.EXPIRES_AT], str):
                raise ValueError(cls.INVALID_EXPIRY)
            expires = datetime.fromisoformat(record[cls.Field.EXPIRES_AT])
            if expires.tzinfo is cls.ABSENT:
                raise ValueError(cls.EXPIRY_TIMEZONE)
        if cls.Field.METADATA in record and not isinstance(record[cls.Field.METADATA], dict):
            raise ValueError(cls.INVALID_METADATA)
        if not isinstance(record.get(cls.Field.SOURCE), dict) or not isinstance(record.get(cls.Field.TAGS), list) or any(
                not isinstance(tag, str) or not tag.strip() for tag in record[cls.Field.TAGS]):
            raise ValueError(cls.INVALID_SOURCE_TAGS)

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    @property
    def updated_at(self):
        return self._value[self.Field.UPDATED_AT]

    @classmethod
    def create(cls, *, project_scope, confirmed, payload, identifier, generation, at, source):
        return {cls.Field.KIND: cls.INITIAL_KIND, cls.Field.SCOPE: project_scope,
                cls.Field.STATUS: confirmed, cls.Field.TAGS: [], **payload,
                cls.Field.ID: identifier, cls.Field.GENERATION: generation,
                cls.Field.REVISION: cls.MINIMUM_REVISION, cls.Field.CREATED_AT: at,
                cls.Field.UPDATED_AT: at, cls.Field.DELETED: cls.INITIAL_DELETED, cls.Field.SOURCE: source}

    def managed_fields(self, fields):
        return {key: self._value[key] for key in fields}

    @staticmethod
    def replace(managed, payload):
        return {**managed, **payload}

    def require_unchanged_scope(self, changes):
        if any(key in changes and changes[key] != self._value.get(key) for key in self.SCOPE_FIELDS):
            raise ValueError(self.SCOPE_CHANGED)

    def require_edit_state(self, restoring):
        if self.deleted != restoring:
            raise ValueError(self.DELETED_RECORD if self.deleted else self.LIVE_RECORD)

    def advance_revision(self):
        self._value[self.Field.REVISION] += self.REVISION_INCREMENT

    @property
    def deleted(self):
        return self._value[self.Field.DELETED]

    @deleted.setter
    def deleted(self, value):
        self._value[self.Field.DELETED] = value

    @updated_at.setter
    def updated_at(self, value):
        self._value[self.Field.UPDATED_AT] = value

    @property
    def status(self):
        return self._value[self.Field.STATUS]

    @property
    def optional_source(self):
        return self._value.get(self.Field.SOURCE)

    @property
    def content(self):
        return self._value[self.Field.CONTENT]

    @property
    def kind(self):
        return self._value[self.Field.KIND]

    @property
    def tags(self):
        return self._value[self.Field.TAGS]

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def generation(self):
        return self._value[self.Field.GENERATION]

    @property
    def version(self):
        return self.generation, self.revision

    @property
    def scope(self):
        return self._value[self.Field.SCOPE]

    @property
    def session_id(self):
        return self._value.get(self.Field.SESSION_ID)

    @property
    def replacements(self):
        return self._value.get(self.Field.REPLACES, [])

    def content_identity(self, normalize):
        return self.scope, self.session_id, normalize(self.content)

    @property
    def metadata(self):
        return self._value[self.Field.METADATA]

    @property
    def metadata_or_empty(self):
        return self._value.get(self.Field.METADATA, {})

    def ensure_metadata(self):
        return self._value.setdefault(self.Field.METADATA, {})

    @status.setter
    def status(self, value):
        self._value[self.Field.STATUS] = value

    @property
    def scope_identity(self):
        return self.scope, self.session_id

    @classmethod
    def reference(cls, identifier, generation, revision):
        return {cls.Field.ID: identifier, cls.Field.GENERATION: generation, cls.Field.REVISION: revision}

    def to_reference(self):
        return self.reference(self.identifier, self.generation, self.revision)

    @property
    def optional_identifier(self):
        return self._value.get(self.Field.ID)

    def content_reference(self):
        return {**self.to_reference(), self.Field.CONTENT: self.content}


class MemorySearchHit:
    class Field:
        SCORE = "score"
        MEMORY = "memory"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, score, memory):
        return {cls.Field.SCORE: score, cls.Field.MEMORY: memory}

    @property
    def memory(self):
        return self._value[self.Field.MEMORY]

    @property
    def rank(self):
        return -self._value[self.Field.SCORE], MemoryRecord(self.memory).identifier


class MemoryContextReference:
    TYPE = "type"
    KIND = "memory"

    @classmethod
    def project(cls, record):
        return {cls.TYPE: cls.KIND, **record.content_reference()}
