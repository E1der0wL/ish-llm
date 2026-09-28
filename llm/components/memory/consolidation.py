"""명시적으로 승인한 기억 병합을 저널로 복구한다. 모델은 병합을 확정하지 않는다."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from llm.services.infrastructure.storage import atomic_json, read_json, sync_directory


class MemoryConsolidation:
    def __init__(self, component, project):
        self.component, self.project = component, project
        self.root = component._checked(component.root(project) / "consolidations")

    def _digest(self, value):
        return hashlib.sha256(Component.serialize(value).encode()).hexdigest()

    def _envelope(self, identifier):
        # 복구 중에는 공개 읽기 차단을 우회하되 기존 레코드 검증은 그대로 수행한다.
        return Component.load(self.component, self.project, identifier)

    def _recover(self, journal):
        component, project = self.component, self.project
        if journal["identity"] != component.identity(project):
            raise ValueError("Memory storage changed during consolidation")
        identifier = validate_name(journal["id"])
        for item in journal["entries"]:
            current = self._digest(self._envelope(item["id"]))
            if current not in (item["before"], self._digest(item["after"])):
                raise ValueError("Memory changed outside the pending consolidation")
        for item in journal["entries"]:
            if self._digest(self._envelope(item["id"])) != self._digest(item["after"]):
                component._write(project, item["id"], item["after"])
        receipt = {"id": identifier, "status": "completed", "proposal": journal["proposal"],
                   "superseded": journal["superseded"], "source": journal["source"]}
        atomic_json(component._checked(self.root / "receipts" / (identifier + ".json")), receipt)
        pending = component._checked(self.root / "pending" / (identifier + ".json"))
        pending.unlink()
        sync_directory(pending.parent)
        return receipt

    def pending(self):
        return sorted(self.component._checked(self.root / "pending").glob("*.json"))

    def apply(self, proposal, *, expected_revision, task_id=None, source=None):
        from .component import MemoryConflictError
        if self.pending():
            raise MemoryConflictError("Recover pending consolidation before editing memories")
        component, project = self.component, self.project
        candidate = component.load(project, proposal, task_id=task_id)
        component._check_revision(candidate, expected_revision)
        if candidate["status"] != "candidate" or component._expired(candidate):
            raise ValueError("Consolidation requires an unexpired candidate")
        references = candidate.get("metadata", {}).get("replaces", [])
        if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
            raise ValueError("Invalid consolidation references")
        names = [r.get("id") for r in references]
        if len(names) != len(set(names)) or proposal in names:
            raise ValueError("Consolidation references must be distinct")
        for ref in references:
            record = component.load(project, ref["id"], task_id=task_id)
            component._check_revision(record, ref["revision"])
            if (record["scope"], record.get("task_id")) != (candidate["scope"], candidate.get("task_id")):
                raise ValueError("Consolidation cannot cross memory scopes")
        operation_id = new_id()
        source = component._source(project, {**(source or {}), "kind": "consolidation", "operation_id": operation_id})
        entries = []
        for identifier in [proposal, *names]:
            before = self._envelope(identifier)
            after = deepcopy(before)
            record = after["record"]
            record["revision"] += 1
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            record["deleted"] = identifier != proposal
            if identifier == proposal:
                record["status"] = "confirmed"
                record.setdefault("metadata", {})["consolidation_id"] = operation_id
            else:
                record.setdefault("metadata", {})["superseded_by"] = proposal
            after["history"].append({"revision": record["revision"], "operation": "update" if identifier == proposal else "delete",
                "at": record["updated_at"], "source": source, "data": deepcopy(record)})
            component.validate_record(identifier, after)
            entries.append({"id": identifier, "before": self._digest(before), "after": after})
        journal = {"id": operation_id, "identity": component.identity(project), "entries": entries,
                   "proposal": proposal, "superseded": names, "source": source}
        atomic_json(component._checked(self.root / "pending" / (operation_id + ".json")), journal)
        return self._recover(journal)

    def recover(self):
        receipts = []
        for path in self.pending():
            journal = read_json(self.component._checked(path))
            if journal.get("id") != path.stem:
                raise ValueError("Consolidation journal identity does not match its path")
            receipts.append(self._recover(journal))
        return receipts
