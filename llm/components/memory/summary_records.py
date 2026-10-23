"""Memory summary records, derived cursors and projections; storage and CAS stay outside."""

from copy import deepcopy
import re


class SummaryCursor:
    INITIAL_OFFSET = 0
    ABSENT = None
    INVALID = "Invalid partial summary cursor"

    class Field:
        DIGEST = "digest"
        OFFSET = "offset"
        SUMMARY = "summary"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, digest, offset, summary):
        return {cls.Field.DIGEST: digest, cls.Field.OFFSET: offset, cls.Field.SUMMARY: summary}

    @classmethod
    def validate_optional(cls, value):
        if value is not cls.ABSENT and (not isinstance(value, dict) or type(value.get(cls.Field.OFFSET)) is not int
                or value[cls.Field.OFFSET] <= cls.INITIAL_OFFSET or not isinstance(value.get(cls.Field.SUMMARY), str)
                or not isinstance(value.get(cls.Field.DIGEST), str)):
            raise ValueError(cls.INVALID)

    def offset_for(self, signature):
        return self._value.get(self.Field.OFFSET, self.INITIAL_OFFSET) if self._value.get(self.Field.DIGEST) == signature else self.INITIAL_OFFSET

    @property
    def summary(self):
        return self._value[self.Field.SUMMARY]


class SummaryMetadata:
    EMPTY_COVERAGE = 0
    INVALID_METADATA = "Invalid Session summary metadata"
    INVALID_COVERAGE = "Invalid Session summary coverage"
    EMPTY_WITHOUT_CURSOR = "Empty summary requires a partial cursor"
    INVALID_SOURCE = "Invalid Session summary source"

    class Field:
        COVERAGE_COUNT = "coverage_count"
        COVERAGE_HASH = "coverage_hash"
        PROFILE = "profile"
        FIRST_MESSAGE_ID = "first_message_id"
        THROUGH_MESSAGE_ID = "through_message_id"
        PARTIAL = "partial"
        INPUT_TRUNCATED = "input_truncated"
        SOURCES = "sources"

    REQUIRED_SOURCES = (Field.COVERAGE_HASH, Field.PROFILE, Field.THROUGH_MESSAGE_ID, Field.FIRST_MESSAGE_ID)

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, coverage_count, coverage_hash, profile, first_message_id, through_message_id, partial, input_truncated):
        return {cls.Field.COVERAGE_COUNT: coverage_count, cls.Field.COVERAGE_HASH: coverage_hash,
                cls.Field.PROFILE: profile, cls.Field.FIRST_MESSAGE_ID: first_message_id,
                cls.Field.THROUGH_MESSAGE_ID: through_message_id, cls.Field.PARTIAL: partial,
                cls.Field.INPUT_TRUNCATED: input_truncated}

    @classmethod
    def from_sources(cls, sources):
        return {cls.Field.SOURCES: sources}

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict):
            raise ValueError(cls.INVALID_METADATA)
        from .work_state import WorkStateMetadata
        WorkStateMetadata.validate_present(value)
        if type(value.get(cls.Field.COVERAGE_COUNT)) is not int or value[cls.Field.COVERAGE_COUNT] < cls.EMPTY_COVERAGE:
            raise ValueError(cls.INVALID_COVERAGE)
        partial = value.get(cls.Field.PARTIAL)
        if value[cls.Field.COVERAGE_COUNT] == cls.EMPTY_COVERAGE and not partial:
            raise ValueError(cls.EMPTY_WITHOUT_CURSOR)
        SummaryCursor.validate_optional(partial)
        for key in cls.REQUIRED_SOURCES:
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(cls.INVALID_SOURCE)

    @property
    def profile(self):
        return self._value[self.Field.PROFILE]

    @property
    def coverage_count(self):
        return self._value[self.Field.COVERAGE_COUNT]

    @property
    def coverage_hash(self):
        return self._value[self.Field.COVERAGE_HASH]

    @property
    def partial_cursor(self):
        return SummaryCursor(self._value.get(self.Field.PARTIAL) or {})

    @property
    def sources(self):
        return self._value.get(self.Field.SOURCES, {})

    @sources.setter
    def sources(self, value):
        self._value[self.Field.SOURCES] = value

    def update(self, value):
        self._value.update(value)

    def to_record(self):
        return self._value


class SummaryRecord:
    GENERATION_PATTERN = r"[a-f0-9]{32}"
    ABSENT = None
    INITIAL_REVISION = 0
    MINIMUM_REVISION = 1
    REVISION_INCREMENT = 1
    INVALID_GENERATION = "Invalid Session summary generation"
    INVALID_IDENTITY = "Invalid Session summary identity"
    INVALID_CONTENT = "Invalid Session summary content"
    INVALID_PROVENANCE = "Invalid Session summary provenance"
    CHANGED = "Session summary changed during processing"

    class Field:
        ID = "id"
        CONTENT = "content"
        METADATA = "metadata"
        SOURCE = "source"
        SESSION_ID = "session_id"
        REVISION = "revision"
        GENERATION = "generation"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, *, identifier, content, metadata):
        return {cls.Field.ID: identifier, cls.Field.CONTENT: content, cls.Field.METADATA: metadata}

    @classmethod
    def with_source(cls, value, source):
        return {**deepcopy(value), cls.Field.SOURCE: deepcopy(source)}

    @classmethod
    def next_revision(cls, revision):
        return revision + cls.REVISION_INCREMENT

    @classmethod
    def stamp(cls, value, *, session_id, revision, generation):
        value.update({cls.Field.SESSION_ID: session_id, cls.Field.REVISION: revision, cls.Field.GENERATION: generation})

    @classmethod
    def validate(cls, session_id, value):
        if (not isinstance(value, dict) or not isinstance(value.get(cls.Field.GENERATION), str)
                or re.fullmatch(cls.GENERATION_PATTERN, value[cls.Field.GENERATION]) is cls.ABSENT):
            raise ValueError(cls.INVALID_GENERATION)
        if (value.get(cls.Field.SESSION_ID) != session_id or type(value.get(cls.Field.REVISION)) is not int
                or value[cls.Field.REVISION] < cls.MINIMUM_REVISION):
            raise ValueError(cls.INVALID_IDENTITY)
        if not isinstance(value.get(cls.Field.CONTENT), str) or not value[cls.Field.CONTENT].strip():
            raise ValueError(cls.INVALID_CONTENT)
        SummaryMetadata.validate(value.get(cls.Field.METADATA))
        if not isinstance(value.get(cls.Field.SOURCE), dict):
            raise ValueError(cls.INVALID_PROVENANCE)

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @property
    def content(self):
        return self._value[self.Field.CONTENT]

    @property
    def metadata(self):
        return SummaryMetadata(self._value[self.Field.METADATA])

    @property
    def generation(self):
        return self._value[self.Field.GENERATION]

    @property
    def revision(self):
        return self._value[self.Field.REVISION]

    @property
    def observed_revision(self):
        return self.revision if self._value else self.INITIAL_REVISION

    @property
    def observed_generation(self):
        return self.generation if self._value else self.ABSENT

    @property
    def optional_source(self):
        return self._value.get(self.Field.SOURCE)


class SummaryProfile:
    FORMAT = 5

    class Field:
        FORMAT = "format"
        PARAMETERS = "settings"
        GOALS = "goals"

    @classmethod
    def project(cls, config, goals):
        return {cls.Field.FORMAT: cls.FORMAT, cls.Field.PARAMETERS: config, cls.Field.GOALS: goals}


class SummaryObservation:
    OVERSIZED = True

    class Field:
        REUSED = "reused"
        COVERED = "covered"
        OVERSIZED_TURN = "oversized_turn"
        GENERATION = SummaryRecord.Field.GENERATION
        REVISION = SummaryRecord.Field.REVISION
        SUMMARY_ID = "summary_id"
        SUMMARY = SummaryCursor.Field.SUMMARY
        SOURCE_RANGE = "source_range"

    @classmethod
    def reused(cls, covered):
        return {cls.Field.REUSED: bool(covered), cls.Field.COVERED: covered}

    @classmethod
    def oversized(cls, covered):
        return {**cls.reused(covered), cls.Field.OVERSIZED_TURN: cls.OVERSIZED}

    @classmethod
    def completed(cls, record, *, covered, summary, source_record):
        return {cls.Field.COVERED: covered, cls.Field.GENERATION: record.generation,
                cls.Field.REVISION: record.revision, cls.Field.SUMMARY_ID: record.identifier,
                cls.Field.SUMMARY: summary, cls.Field.SOURCE_RANGE: deepcopy(source_record.metadata.to_record())}


class SummaryContextReference:
    TYPE = "type"
    KIND = "conversation_summary"

    @classmethod
    def project(cls, record):
        return {cls.TYPE: cls.KIND, SummaryRecord.Field.CONTENT: record.content,
                SummaryRecord.Field.ID: record.identifier, SummaryRecord.Field.GENERATION: record.generation,
                SummaryRecord.Field.REVISION: record.revision, SummaryMetadata.Field.SOURCES: deepcopy(record.metadata.sources)}
