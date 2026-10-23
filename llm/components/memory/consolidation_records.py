"""Reviewed Memory consolidation records; no storage or recovery effects."""

from datetime import datetime
import re

from llm.components.base import validate_name
from .records import MemoryRecord
from .history import MemoryEnvelope, MemoryHistory
from .sources import MemorySource


class ConsolidationOperation:
    ID_PATTERN = r"[a-f0-9]{32}"
    INVALID = "Invalid consolidation operation"
    INVALID_TARGETS = "Invalid consolidation targets"
    INVALID_SOURCE = "Invalid consolidation provenance"

    class Field:
        ID = "id"
        PROPOSAL = "proposal"
        SUPERSEDED = "superseded"
        SOURCE = "source"

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, identifier, proposal, superseded, source):
        return {cls.Field.ID: identifier, cls.Field.SOURCE: source,
                cls.Field.PROPOSAL: proposal, cls.Field.SUPERSEDED: superseded}

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def proposal(self):
        return self._value[self.Field.PROPOSAL]

    @property
    def superseded(self):
        return self._value[self.Field.SUPERSEDED]

    @property
    def source(self):
        return self._value[self.Field.SOURCE]

    @classmethod
    def validate(cls, value, *, kind):
        if (not isinstance(value.get(cls.Field.ID), str)
                or re.fullmatch(cls.ID_PATTERN, value[cls.Field.ID]) is None
                or not isinstance(value.get(cls.Field.SUPERSEDED), list)
                or not isinstance(value.get(cls.Field.SOURCE), dict)):
            raise ValueError(cls.INVALID)
        proposal = validate_name(value.get(cls.Field.PROPOSAL))
        names = [validate_name(name) for name in value[cls.Field.SUPERSEDED]]
        if proposal in names or len(set(names)) != len(names):
            raise ValueError(cls.INVALID_TARGETS)
        if (MemorySource(value[cls.Field.SOURCE]).kind != kind
                or MemorySource(value[cls.Field.SOURCE]).operation_id != value[cls.Field.ID]):
            raise ValueError(cls.INVALID_SOURCE)
        validate_name(MemorySource(value[cls.Field.SOURCE]).project_id)


class ConsolidationMetadata:
    CONSOLIDATION_ID = "consolidation_id"
    SUPERSEDED_BY = "superseded_by"
    UNRELATED_CHANGE = "Consolidation deletion changed unrelated metadata"

    @classmethod
    def confirm(cls, record, operation_id):
        MemoryRecord(record).ensure_metadata()[cls.CONSOLIDATION_ID] = operation_id

    @classmethod
    def supersede(cls, record, proposal):
        MemoryRecord(record).ensure_metadata()[cls.SUPERSEDED_BY] = proposal

    @classmethod
    def deletion(cls, previous, record, source, validate_operation):
        proposal = MemoryRecord(record).metadata.get(cls.SUPERSEDED_BY)
        validate_operation(ConsolidationOperation.create(MemorySource(source).operation_id,
            proposal, [MemoryRecord(previous).identifier], source))
        expected = {**MemoryRecord(previous).metadata_or_empty, cls.SUPERSEDED_BY: proposal}
        if MemoryRecord(record).metadata != expected:
            raise ValueError(cls.UNRELATED_CHANGE)
        return expected


class ConsolidationEntry:
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    INVALID = "Invalid consolidation entry"

    class Field:
        ID = MemoryRecord.Field.ID
        BEFORE = "before"
        AFTER = "after"

    FIELDS = frozenset((Field.ID, Field.BEFORE, Field.AFTER))

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, identifier, before, after):
        return {cls.Field.ID: identifier, cls.Field.BEFORE: before, cls.Field.AFTER: after}

    def validate(self):
        value = self._value
        if (not isinstance(value, dict) or value.keys() != self.FIELDS
                or not isinstance(value[self.Field.BEFORE], str)
                or re.fullmatch(self.DIGEST_PATTERN, value[self.Field.BEFORE]) is None):
            raise ValueError(self.INVALID)

    @property
    def identifier(self):
        return self._value[self.Field.ID]

    @property
    def before(self):
        return self._value[self.Field.BEFORE]

    @property
    def after(self):
        return self._value[self.Field.AFTER]


class ConsolidationJournal(ConsolidationOperation):
    INVALID_FIELDS = "Invalid consolidation journal fields"
    INVALID_IDENTITY = "Invalid consolidation identity or entries"
    INCOMPLETE_AFTER = "Consolidation requires the complete after envelope"
    NO_PRIOR_REVISION = "Consolidation requires a prior revision"
    INVALID_TIME = "Consolidation time requires a timezone"
    UNREVIEWED_CHANGE = "Consolidation entry is not the reviewed change"
    NOT_CANDIDATE = "Consolidation proposal was not a candidate"
    INCOMPLETE_TARGETS = "Consolidation entries must exactly cover reviewed targets"
    CHANGED_TARGETS = "Consolidation targets differ from the reviewed proposal"

    class Field(ConsolidationOperation.Field):
        IDENTITY = "identity"
        ENTRIES = "entries"

    FIELDS = frozenset((Field.ID, Field.IDENTITY, Field.ENTRIES, Field.PROPOSAL,
                        Field.SUPERSEDED, Field.SOURCE))

    @classmethod
    def create(cls, identifier, identity, entries, proposal, superseded, source):
        return {cls.Field.ID: identifier, cls.Field.IDENTITY: identity, cls.Field.ENTRIES: entries,
                cls.Field.PROPOSAL: proposal, cls.Field.SUPERSEDED: superseded, cls.Field.SOURCE: source}

    @property
    def entries(self):
        for value in self._value[self.Field.ENTRIES]:
            yield ConsolidationEntry(value)


    @classmethod
    def validate(cls, value, *, component, project, operation, after_record, digest):
        if value.keys() != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        operation(value)
        if value[cls.Field.IDENTITY] != component.identity(project) or not isinstance(value[cls.Field.ENTRIES], list):
            raise ValueError(cls.INVALID_IDENTITY)
        identifiers, before_records = [], {}
        for entry in value[cls.Field.ENTRIES]:
            ConsolidationEntry(entry).validate()
            identifier = validate_name(ConsolidationEntry(entry).identifier)
            identifiers.append(identifier)
            after = ConsolidationEntry(entry).after
            if not isinstance(after, dict):
                raise ValueError(cls.INCOMPLETE_AFTER)
            component.validate_record(identifier, after)
            if len(MemoryEnvelope(after).history) < MemoryHistory.MINIMUM_TRANSITION_ENTRIES:
                raise ValueError(cls.NO_PRIOR_REVISION)
            before = MemoryEnvelope(after).previous()
            component.validate_record(identifier, before)
            before_records[identifier] = MemoryEnvelope(before).record
            at = MemoryRecord(MemoryEnvelope(after).record).updated_at
            if not isinstance(at, str) or datetime.fromisoformat(at).utcoffset() is None:
                raise ValueError(cls.INVALID_TIME)
            if (digest(before) != ConsolidationEntry(entry).before or MemoryRecord(MemoryEnvelope(before).record).deleted
                    or after != after_record(identifier, before, value[cls.Field.PROPOSAL], value[cls.Field.SOURCE], at, value[cls.Field.ID])):
                raise ValueError(cls.UNREVIEWED_CHANGE)
            if identifier == value[cls.Field.PROPOSAL] and MemoryRecord(MemoryEnvelope(before).record).status != component.CANDIDATE:
                raise ValueError(cls.NOT_CANDIDATE)
        if identifiers != [value[cls.Field.PROPOSAL], *value[cls.Field.SUPERSEDED]]:
            raise ValueError(cls.INCOMPLETE_TARGETS)
        proposal = before_records[value[cls.Field.PROPOSAL]]
        references = [MemoryRecord.reference(name, MemoryRecord(before_records[name]).generation,
                       MemoryRecord(before_records[name]).revision) for name in value[cls.Field.SUPERSEDED]]
        if MemoryRecord(proposal).replacements != references or any(
                MemoryRecord(record).scope_identity != MemoryRecord(proposal).scope_identity
                for record in before_records.values()):
            raise ValueError(cls.CHANGED_TARGETS)

class ConsolidationReceipt(ConsolidationOperation):
    COMPLETED = "completed"
    INVALID = "Invalid consolidation receipt"

    class Field(ConsolidationOperation.Field):
        STATUS = "status"

    FIELDS = frozenset((Field.ID, Field.STATUS, Field.PROPOSAL, Field.SUPERSEDED, Field.SOURCE))

    @classmethod
    def from_journal(cls, identifier, journal, *, completed):
        value = ConsolidationJournal(journal)
        return {cls.Field.ID: identifier, cls.Field.STATUS: completed, cls.Field.PROPOSAL: value.proposal,
                cls.Field.SUPERSEDED: value.superseded, cls.Field.SOURCE: value.source}

    @classmethod
    def validate(cls, value, *, completed, operation):
        if value.keys() != cls.FIELDS or value[cls.Field.STATUS] != completed:
            raise ValueError(cls.INVALID)
        operation(value)
