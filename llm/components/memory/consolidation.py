"""명시적으로 승인한 기억 병합을 저널로 복구한다. 모델은 병합을 확정하지 않는다."""

from copy import deepcopy
from .records import MemoryRecord
from .history import MemoryHistory, MemoryEnvelope
from datetime import datetime, timezone
import hashlib
import re

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from llm.services.infrastructure.storage import atomic_json, make_directory, revision_token, unlink_file


class MemoryConsolidation:
    SCHEMA_VERSION = 1
    MANIFEST = "manifest.json"
    PENDING = "pending"
    RECEIPTS = "receipts"
    COMPLETED = "completed"
    KIND = "consolidation"
    MANIFEST_FIELDS = frozenset(("schema_version", "project_id", "component", PENDING, RECEIPTS))

    def __init__(self, component, project):
        self.component, self.project = component, project
        self.root = component._checked(component.root(project) / "consolidations")

    def initialize(self):
        if self.root.exists():
            raise ValueError("Cannot adopt existing Memory consolidation storage")
        for group in (self.PENDING, self.RECEIPTS):
            make_directory(self.root / group)
        atomic_json(self.root / self.MANIFEST, {"schema_version": self.SCHEMA_VERSION,
            "project_id": self.project.id, "component": self.component.name, self.PENDING: {}, self.RECEIPTS: {}})

    def _json(self, path):
        path = self.component._checked(path)
        if not path.is_file():
            raise ValueError("Consolidation evidence must be a regular file")
        try:
            return Component.deserialize(path.read_text(encoding="utf-8"))
        except TypeError as error:
            raise ValueError("Invalid consolidation JSON") from error

    @classmethod
    def _operation(cls, value):
        if (not isinstance(value.get("id"), str) or re.fullmatch(r"[a-f0-9]{32}", value["id"]) is None
                or not isinstance(value.get("superseded"), list) or not isinstance(value.get("source"), dict)):
            raise ValueError("Invalid consolidation operation")
        proposal = validate_name(value.get("proposal"))
        names = [validate_name(name) for name in value["superseded"]]
        if proposal in names or len(set(names)) != len(names):
            raise ValueError("Invalid consolidation targets")
        if (value["source"].get("kind") != cls.KIND
                or value["source"].get("operation_id") != value["id"]):
            raise ValueError("Invalid consolidation provenance")
        validate_name(value["source"].get("project_id"))

    @classmethod
    def deletion_metadata(cls, previous, record, source):
        """Validate the metadata change owned by a consolidation deletion."""
        proposal = MemoryRecord(record).metadata.get("superseded_by")
        cls._operation({"id": source.get("operation_id"), "source": source,
                        "proposal": proposal, "superseded": [MemoryRecord(previous).identifier]})
        expected = {**MemoryRecord(previous).metadata_or_empty, "superseded_by": proposal}
        if MemoryRecord(record).metadata != expected:
            raise ValueError("Consolidation deletion changed unrelated metadata")
        return expected

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
            current.ensure_metadata()["consolidation_id"] = operation_id
        else:
            current.ensure_metadata()["superseded_by"] = proposal
        selected.history.append(MemoryHistory.entry(revision=current.revision,
            operation=self.component.UPDATE if identifier == proposal else self.component.DELETE,
            at=current.updated_at, source=source, data=deepcopy(record)))
        self.component.validate_record(identifier, after)
        return after

    def _validate_journal(self, value):
        if value.keys() != {"id", "identity", "entries", "proposal", "superseded", "source"}:
            raise ValueError("Invalid consolidation journal fields")
        self._operation(value)
        if value["identity"] != self.component.identity(self.project) or not isinstance(value["entries"], list):
            raise ValueError("Invalid consolidation identity or entries")
        identifiers, before_records = [], {}
        for entry in value["entries"]:
            if (not isinstance(entry, dict) or entry.keys() != {"id", "before", "after"}
                    or not isinstance(entry["before"], str) or re.fullmatch(r"[a-f0-9]{64}", entry["before"]) is None):
                raise ValueError("Invalid consolidation entry")
            identifier = validate_name(entry["id"])
            identifiers.append(identifier)
            after = entry["after"]
            if not isinstance(after, dict):
                raise ValueError("Consolidation requires the complete after envelope")
            self.component.validate_record(identifier, after)
            if len(MemoryEnvelope(after).history) < MemoryHistory.MINIMUM_TRANSITION_ENTRIES:
                raise ValueError("Consolidation requires a prior revision")
            before = MemoryEnvelope(after).previous()
            self.component.validate_record(identifier, before)
            before_records[identifier] = MemoryEnvelope(before).record
            at = MemoryRecord(MemoryEnvelope(after).record).updated_at
            if not isinstance(at, str) or datetime.fromisoformat(at).utcoffset() is None:
                raise ValueError("Consolidation time requires a timezone")
            if (self._digest(before) != entry["before"] or MemoryRecord(MemoryEnvelope(before).record).deleted
                    or after != self._after(identifier, before, value["proposal"], value["source"], at, value["id"])):
                raise ValueError("Consolidation entry is not the reviewed change")
            if identifier == value["proposal"] and MemoryRecord(MemoryEnvelope(before).record).status != self.component.CANDIDATE:
                raise ValueError("Consolidation proposal was not a candidate")
        if identifiers != [value["proposal"], *value["superseded"]]:
            raise ValueError("Consolidation entries must exactly cover reviewed targets")
        proposal = before_records[value["proposal"]]
        references = [MemoryRecord.reference(name, MemoryRecord(before_records[name]).generation,
                       MemoryRecord(before_records[name]).revision) for name in value["superseded"]]
        if MemoryRecord(proposal).replacements != references or any(
                MemoryRecord(record).scope_identity != MemoryRecord(proposal).scope_identity
                for record in before_records.values()):
            raise ValueError("Consolidation targets differ from the reviewed proposal")

    def _validate_receipt(self, value):
        if (value.keys() != {"id", "status", "proposal", "superseded", "source"}
                or value["status"] != self.COMPLETED):
            raise ValueError("Invalid consolidation receipt")
        self._operation(value)

    def inventory(self):
        manifest = self._json(self.root / self.MANIFEST)
        if (manifest.keys() != self.MANIFEST_FIELDS
                or type(manifest["schema_version"]) is not int or manifest["schema_version"] != self.SCHEMA_VERSION
                or manifest["project_id"] != self.project.id or manifest["component"] != self.component.name
                or not isinstance(manifest[self.PENDING], dict) or not isinstance(manifest[self.RECEIPTS], dict)
                or manifest[self.PENDING].keys() & manifest[self.RECEIPTS].keys()):
            raise ValueError("Invalid consolidation manifest")
        if {self.component._checked(path).name for path in self.root.iterdir()} != {self.MANIFEST, self.PENDING, self.RECEIPTS}:
            raise ValueError("Consolidation storage is missing or undeclared")
        groups = {}
        for group, validate in ((self.PENDING, self._validate_journal), (self.RECEIPTS, self._validate_receipt)):
            directory = self.component._checked(self.root / group)
            if not directory.is_dir():
                raise ValueError("Consolidation directory is missing")
            paths = {}
            for path in directory.iterdir():
                self.component._checked(path)
                if path.suffix != ".json" or not path.is_file():
                    raise ValueError("Invalid consolidation entry path")
                paths[validate_name(path.stem)] = path
            if paths.keys() != manifest[group].keys():
                raise ValueError("Consolidation evidence is missing or undeclared")
            records = {}
            for identifier, revision in manifest[group].items():
                value = self._json(paths[identifier])
                validate(value)
                if value["id"] != identifier or revision_token(value) != revision:
                    raise ValueError("Consolidation identity or revision changed")
                records[identifier] = value
            groups[group] = records
        return manifest, groups[self.PENDING], groups[self.RECEIPTS]

    def _store_journal(self, journal):
        manifest, pending, receipts = self.inventory()
        self._validate_journal(journal)
        identifier = journal["id"]
        if identifier in pending or identifier in receipts:
            raise FileExistsError("Consolidation already exists")
        manifest[self.PENDING][identifier] = revision_token(journal)
        atomic_json(self.root / self.PENDING / (identifier + ".json"), journal)
        atomic_json(self.root / self.MANIFEST, manifest)

    def _digest(self, value):
        return hashlib.sha256(Component.serialize(value).encode()).hexdigest()

    def _envelope(self, identifier):
        # 복구 중에는 공개 읽기 차단을 우회하되 기존 레코드 검증은 그대로 수행한다.
        return Component.load(self.component, self.project, identifier)

    def _recover(self, journal):
        component, project = self.component, self.project
        manifest, pending, _ = self.inventory()
        if pending.get(journal["id"]) != journal:
            raise ValueError("Recovery requires the declared pending journal")
        identifier = validate_name(journal["id"])
        for item in journal["entries"]:
            current = self._digest(self._envelope(item["id"]))
            if current not in (item["before"], self._digest(item["after"])):
                raise ValueError("Memory changed outside the pending consolidation")
        for item in journal["entries"]:
            if self._digest(self._envelope(item["id"])) != self._digest(item["after"]):
                component._write(project, item["id"], item["after"])
        receipt = {"id": identifier, "status": self.COMPLETED, "proposal": journal["proposal"],
                   "superseded": journal["superseded"], "source": journal["source"]}
        self._validate_receipt(receipt)
        atomic_json(component._checked(self.root / self.RECEIPTS / (identifier + ".json")), receipt)
        unlink_file(component._checked(self.root / self.PENDING / (identifier + ".json")))
        del manifest[self.PENDING][identifier]
        manifest[self.RECEIPTS][identifier] = revision_token(receipt)
        atomic_json(self.root / self.MANIFEST, manifest)
        return receipt

    def pending(self):
        if not self.component._has_storage(self.project):
            return []
        return [self.root / self.PENDING / (identifier + ".json") for identifier in sorted(self.inventory()[1])]

    def apply(self, proposal, *, expected_revision, expected_generation, session_id=None, source=None):
        from .component import MemoryConflictError
        if self.pending():
            raise MemoryConflictError("Recover pending consolidation before editing memories")
        component, project = self.component, self.project
        candidate = component.load(project, proposal, session_id=session_id)
        component._check_revision(candidate, expected_revision, expected_generation)
        if MemoryRecord(candidate).status != component.CANDIDATE or component._expired(candidate):
            raise ValueError("Consolidation requires an unexpired candidate")
        references = MemoryRecord(candidate).replacements
        if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
            raise ValueError("Invalid consolidation references")
        names = [MemoryRecord(r).optional_identifier for r in references]
        if len(names) != len(set(names)) or proposal in names:
            raise ValueError("Consolidation references must be distinct")
        for ref in references:
            record = component.load(project, MemoryRecord(ref).identifier, session_id=session_id)
            component._check_revision(record, MemoryRecord(ref).revision, MemoryRecord(ref).generation)
            if MemoryRecord(record).scope_identity != MemoryRecord(candidate).scope_identity:
                raise ValueError("Consolidation cannot cross memory scopes")
        operation_id = new_id()
        source = component._source(project, {**(source or {}), "kind": self.KIND, "operation_id": operation_id})
        entries = []
        for identifier in [proposal, *names]:
            before = self._envelope(identifier)
            after = self._after(identifier, before, proposal, source, datetime.now(timezone.utc).isoformat(), operation_id)
            entries.append({"id": identifier, "before": self._digest(before), "after": after})
        journal = {"id": operation_id, "identity": component.identity(project), "entries": entries,
                   "proposal": proposal, "superseded": names, "source": source}
        self._store_journal(journal)
        return self._recover(journal)

    def recover(self):
        receipts = []
        for journal in self.inventory()[1].values():
            receipts.append(self._recover(journal))
        return receipts

    def clone_receipts(self, destination):
        _, pending, receipts = self.inventory()
        if pending:
            raise ValueError("Recover pending consolidation before cloning")
        target = MemoryConsolidation(self.component, destination)
        manifest, destination_pending, destination_receipts = target.inventory()
        if destination_pending or destination_receipts:
            raise FileExistsError("Destination consolidation storage is not empty")
        for identifier, receipt in receipts.items():
            target._validate_receipt(receipt)
            atomic_json(target.root / self.RECEIPTS / (identifier + ".json"), receipt)
            manifest[self.RECEIPTS][identifier] = revision_token(receipt)
        atomic_json(target.root / self.MANIFEST, manifest)
