"""명시적으로 승인한 기억 병합을 저널로 복구한다. 모델은 병합을 확정하지 않는다."""

from copy import deepcopy
from .records import MemoryRecord
from .history import MemoryHistory, MemoryEnvelope
from .sources import MemorySource
from .consolidation_records import (ConsolidationOperation, ConsolidationMetadata,
                                    ConsolidationEntry, ConsolidationJournal, ConsolidationReceipt)
from .consolidation_storage import ConsolidationManifest, ConsolidationInventory
from datetime import datetime, timezone
import hashlib

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from llm.services.infrastructure.storage import atomic_json, make_directory, revision_token, unlink_file


class MemoryConsolidation:
    NO_SESSION = None
    NO_SOURCE = None
    SCHEMA_VERSION = ConsolidationManifest.SCHEMA_VERSION
    MANIFEST = "manifest.json"
    PENDING = ConsolidationManifest.Field.PENDING
    RECEIPTS = ConsolidationManifest.Field.RECEIPTS
    COMPLETED = ConsolidationReceipt.COMPLETED
    KIND = MemorySource.CONSOLIDATION
    MANIFEST_FIELDS = ConsolidationManifest.FIELDS
    EXISTING_STORAGE = 'Cannot adopt existing Memory consolidation storage'
    NONREGULAR_FILE = 'Consolidation evidence must be a regular file'
    INVALID_JSON = 'Invalid consolidation JSON'
    UNDECLARED_STORAGE = 'Consolidation storage is missing or undeclared'
    MISSING_DIRECTORY = 'Consolidation directory is missing'
    INVALID_PATH = 'Invalid consolidation entry path'
    UNDECLARED_EVIDENCE = 'Consolidation evidence is missing or undeclared'
    CHANGED_IDENTITY = 'Consolidation identity or revision changed'
    ALREADY_EXISTS = 'Consolidation already exists'
    UNDECLARED_RECOVERY = 'Recovery requires the declared pending journal'
    CHANGED_MEMORY = 'Memory changed outside the pending consolidation'
    PENDING_EDIT = 'Recover pending consolidation before editing memories'
    INVALID_CANDIDATE = 'Consolidation requires an unexpired candidate'
    INVALID_REFERENCES = 'Invalid consolidation references'
    DUPLICATE_REFERENCES = 'Consolidation references must be distinct'
    CROSSED_SCOPE = 'Consolidation cannot cross memory scopes'
    PENDING_CLONE = 'Recover pending consolidation before cloning'
    NONEMPTY_DESTINATION = 'Destination consolidation storage is not empty'
    DIRECTORY = 'consolidations'
    SUFFIX = '.json'
    ENCODING = 'utf-8'

    def __init__(self, component, project):
        self.component, self.project = component, project
        self.root = component._checked(component.root(project) / self.DIRECTORY)

    def initialize(self):
        if self.root.exists():
            raise ValueError(self.EXISTING_STORAGE)
        for group in (self.PENDING, self.RECEIPTS):
            make_directory(self.root / group)
        atomic_json(self.root / self.MANIFEST, ConsolidationManifest.create(schema_version=self.SCHEMA_VERSION,
            project_id=self.project.id, component=self.component.name, pending=self.PENDING, receipts=self.RECEIPTS))

    def _json(self, path):
        path = self.component._checked(path)
        if not path.is_file():
            raise ValueError(self.NONREGULAR_FILE)
        try:
            return Component.deserialize(path.read_text(encoding=self.ENCODING))
        except TypeError as error:
            raise ValueError(self.INVALID_JSON) from error

    @classmethod
    def _operation(cls, value):
        ConsolidationOperation.validate(value, kind=cls.KIND)

    @classmethod
    def deletion_metadata(cls, previous, record, source):
        """Validate the metadata change owned by a consolidation deletion."""
        return ConsolidationMetadata.deletion(previous, record, source, cls._operation)

    def _after(self, identifier, before, proposal, source, at, operation_id):
        after = deepcopy(before)
        selected = MemoryEnvelope(after)
        record = selected.record
        current = MemoryRecord(record)
        current.advance_revision()
        current.updated_at = at
        current.deleted = identifier != proposal
        if identifier == proposal:
            current.status = self.component.CONFIRMED
            ConsolidationMetadata.confirm(record, operation_id)
        else:
            ConsolidationMetadata.supersede(record, proposal)
        selected.history.append(MemoryHistory.entry(revision=current.revision,
            operation=self.component.UPDATE if identifier == proposal else self.component.DELETE,
            at=current.updated_at, source=source, data=deepcopy(record)))
        self.component.validate_record(identifier, after)
        return after

    def _validate_journal(self, value):
        ConsolidationJournal.validate(value, component=self.component, project=self.project,
            operation=self._operation, after_record=self._after, digest=self._digest)

    def _validate_receipt(self, value):
        ConsolidationReceipt.validate(value, completed=self.COMPLETED, operation=self._operation)

    def inventory(self):
        manifest = self._json(self.root / self.MANIFEST)
        ConsolidationManifest.validate(manifest, schema_version=self.SCHEMA_VERSION,
            project_id=self.project.id, component=self.component.name, pending=self.PENDING,
            receipts=self.RECEIPTS, fields=self.MANIFEST_FIELDS)
        if {self.component._checked(path).name for path in self.root.iterdir()} != {self.MANIFEST, self.PENDING, self.RECEIPTS}:
            raise ValueError(self.UNDECLARED_STORAGE)
        groups = {}
        for group, validate in ((self.PENDING, self._validate_journal), (self.RECEIPTS, self._validate_receipt)):
            directory = self.component._checked(self.root / group)
            if not directory.is_dir():
                raise ValueError(self.MISSING_DIRECTORY)
            paths = {}
            for path in directory.iterdir():
                self.component._checked(path)
                if path.suffix != self.SUFFIX or not path.is_file():
                    raise ValueError(self.INVALID_PATH)
                paths[validate_name(path.stem)] = path
            if paths.keys() != ConsolidationManifest(manifest).keys(group):
                raise ValueError(self.UNDECLARED_EVIDENCE)
            records = {}
            for identifier, revision in ConsolidationManifest(manifest).items(group):
                value = self._json(paths[identifier])
                validate(value)
                if ConsolidationOperation(value).identifier != identifier or revision_token(value) != revision:
                    raise ValueError(self.CHANGED_IDENTITY)
                records[identifier] = value
            groups[group] = records
        return ConsolidationInventory(manifest, groups[self.PENDING], groups[self.RECEIPTS])

    def _store_journal(self, journal):
        manifest, pending, receipts = self.inventory()
        self._validate_journal(journal)
        identifier = ConsolidationJournal(journal).identifier
        if identifier in pending or identifier in receipts:
            raise FileExistsError(self.ALREADY_EXISTS)
        ConsolidationManifest(manifest).declare(self.PENDING, identifier, revision_token(journal))
        atomic_json(self.root / self.PENDING / (identifier + self.SUFFIX), journal)
        atomic_json(self.root / self.MANIFEST, manifest)

    def _digest(self, value):
        return hashlib.sha256(Component.serialize(value).encode()).hexdigest()

    def _envelope(self, identifier):
        # 복구 중에는 공개 읽기 차단을 우회하되 기존 레코드 검증은 그대로 수행한다.
        return Component.load(self.component, self.project, identifier)

    def _recover(self, journal):
        component, project = self.component, self.project
        manifest, pending, _ = self.inventory()
        if pending.get(ConsolidationJournal(journal).identifier) != journal:
            raise ValueError(self.UNDECLARED_RECOVERY)
        identifier = validate_name(ConsolidationJournal(journal).identifier)
        for item in ConsolidationJournal(journal).entries:
            current = self._digest(self._envelope(item.identifier))
            if current not in (item.before, self._digest(item.after)):
                raise ValueError(self.CHANGED_MEMORY)
        for item in ConsolidationJournal(journal).entries:
            if self._digest(self._envelope(item.identifier)) != self._digest(item.after):
                component._write(project, item.identifier, item.after)
        receipt = ConsolidationReceipt.from_journal(identifier, journal, completed=self.COMPLETED)
        self._validate_receipt(receipt)
        atomic_json(component._checked(self.root / self.RECEIPTS / (identifier + self.SUFFIX)), receipt)
        unlink_file(component._checked(self.root / self.PENDING / (identifier + self.SUFFIX)))
        ConsolidationManifest(manifest).remove(self.PENDING, identifier)
        ConsolidationManifest(manifest).declare(self.RECEIPTS, identifier, revision_token(receipt))
        atomic_json(self.root / self.MANIFEST, manifest)
        return receipt

    def pending(self):
        if not self.component._has_storage(self.project):
            return []
        return [self.root / self.PENDING / (identifier + self.SUFFIX) for identifier in sorted(self.inventory().pending)]

    def apply(self, proposal, *, expected_revision, expected_generation, session_id=NO_SESSION, source=NO_SOURCE):
        from .component import MemoryConflictError
        if self.pending():
            raise MemoryConflictError(self.PENDING_EDIT)
        component, project = self.component, self.project
        candidate = component.load(project, proposal, session_id=session_id)
        component._check_revision(candidate, expected_revision, expected_generation)
        if MemoryRecord(candidate).status != component.CANDIDATE or component._expired(candidate):
            raise ValueError(self.INVALID_CANDIDATE)
        references = MemoryRecord(candidate).replacements
        if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
            raise ValueError(self.INVALID_REFERENCES)
        names = [MemoryRecord(r).optional_identifier for r in references]
        if len(names) != len(set(names)) or proposal in names:
            raise ValueError(self.DUPLICATE_REFERENCES)
        for ref in references:
            record = component.load(project, MemoryRecord(ref).identifier, session_id=session_id)
            component._check_revision(record, MemoryRecord(ref).revision, MemoryRecord(ref).generation)
            if MemoryRecord(record).scope_identity != MemoryRecord(candidate).scope_identity:
                raise ValueError(self.CROSSED_SCOPE)
        operation_id = new_id()
        source = component._source(project, MemorySource.consolidation(source, operation_id, kind=self.KIND))
        entries = []
        for identifier in [proposal, *names]:
            before = self._envelope(identifier)
            after = self._after(identifier, before, proposal, source, datetime.now(timezone.utc).isoformat(), operation_id)
            entries.append(ConsolidationEntry.create(identifier, self._digest(before), after))
        journal = ConsolidationJournal.create(operation_id, component.identity(project), entries, proposal, names, source)
        self._store_journal(journal)
        return self._recover(journal)

    def recover(self):
        receipts = []
        for journal in self.inventory().pending.values():
            receipts.append(self._recover(journal))
        return receipts

    def clone_receipts(self, destination):
        _, pending, receipts = self.inventory()
        if pending:
            raise ValueError(self.PENDING_CLONE)
        target = MemoryConsolidation(self.component, destination)
        manifest, destination_pending, destination_receipts = target.inventory()
        if destination_pending or destination_receipts:
            raise FileExistsError(self.NONEMPTY_DESTINATION)
        for identifier, receipt in receipts.items():
            target._validate_receipt(receipt)
            atomic_json(target.root / self.RECEIPTS / (identifier + self.SUFFIX), receipt)
            ConsolidationManifest(manifest).declare(self.RECEIPTS, identifier, revision_token(receipt))
        atomic_json(target.root / self.MANIFEST, manifest)
