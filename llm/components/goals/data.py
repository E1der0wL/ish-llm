"""Goal CRUD·진행·Run 참조를 같은 Project 트랜잭션과 내용 해시 CAS로 보호한다."""

from copy import deepcopy
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision

from .records import GoalStatus, GoalRecord, GoalRunReference, GoalContextReference


class GoalData(ComponentData):
    UNSPECIFIED = None
    MISSING_VERSION = "Goal mutation requires expected_version"

    def _validate_refs(self, value):
        GoalRecord(value).validate_references(self.reference)

    @workspace_locked
    def create(self, data, *, identifier=UNSPECIFIED):
        project, component = self._current()
        component.validate_record(identifier, data)
        self._validate_refs(data)
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version):
        project, component = self._current()
        component.validate_record(identifier, data)
        self._validate_refs(data)
        if expected_version is self.UNSPECIFIED:
            raise ValueError(self.MISSING_VERSION)
        super().save(identifier, data, expected_version=expected_version)
        return self.snapshot(identifier)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version):
        value = self.load(identifier)
        check_revision(value, expected_version)
        value.update(deepcopy(changes))
        return self.save(identifier, value, expected_version=expected_version)

    @workspace_locked
    def delete(self, identifier, *, expected_version):
        if expected_version is self.UNSPECIFIED:
            raise ValueError(self.MISSING_VERSION)
        return super().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def list(self, *, session_id=UNSPECIFIED, status=UNSPECIFIED):
        if session_id is not self.UNSPECIFIED:
            self.reference(session_id)
        return {key: value for key, value in super().list().items()
                if (session_id is self.UNSPECIFIED or GoalRecord(value).scope.includes(session_id))
                and (status is self.UNSPECIFIED or GoalRecord(value).status == status)}

    @workspace_locked
    def link_run(self, identifier, session_id, run_id, *, relation, expected_version):
        value = self.load(identifier)
        ref = GoalRunReference.create(session_id, run_id, relation)
        refs = GoalRecord(value).run_refs
        return self.update(identifier, GoalRecord.references_change(refs if ref in refs else [*refs, ref]), expected_version=expected_version)

    @workspace_locked
    def unlink_run(self, identifier, session_id, run_id, *, expected_version):
        value = self.load(identifier)
        return self.update(identifier, GoalRecord.references_change([ref for ref in GoalRecord(value).run_refs
            if not GoalRunReference(ref).matches(session_id, run_id)]), expected_version=expected_version)

    def complete(self, identifier, *, expected_version):
        return self.update(identifier, GoalRecord.status_change(GoalStatus.COMPLETED.value), expected_version=expected_version)

    def pause(self, identifier, *, expected_version):
        return self.update(identifier, GoalRecord.status_change(GoalStatus.PAUSED.value), expected_version=expected_version)

    def resume(self, identifier, *, expected_version):
        return self.update(identifier, GoalRecord.status_change(GoalStatus.ACTIVE.value), expected_version=expected_version)

    @workspace_locked
    def context_references(self, session_id, *, identifiers=UNSPECIFIED):
        records = self.list(session_id=session_id, status=GoalStatus.ACTIVE.value)
        return [GoalContextReference.project(key, value)
                for key, value in records.items() if identifiers is self.UNSPECIFIED or key in identifiers]

    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)
    adelete = async_method(delete)
    alist = async_method(list)
    alink_run = async_method(link_run)
    aunlink_run = async_method(unlink_run)
    acomplete = async_method(complete)
    apause = async_method(pause)
    aresume = async_method(resume)
    acontext_references = async_method(context_references)
