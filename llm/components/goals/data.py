"""Goal CRUD·진행·Run 참조를 같은 Project 트랜잭션과 내용 해시 CAS로 보호한다."""

from copy import deepcopy
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision, revision_token


class GoalData(ComponentData):
    def _validate_refs(self, value):
        if value["scope"]["type"] == "session":
            self.reference(value["scope"]["id"])
        for ref in value.get("run_refs", []):
            self.reference(ref["session_id"], ref["run_id"])

    @workspace_locked
    def create(self, data, *, identifier=None):
        project, component = self._current()
        component.validate_record(identifier, data)
        self._validate_refs(data)
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version):
        project, component = self._current()
        component.validate_record(identifier, data)
        self._validate_refs(data)
        if expected_version is None:
            raise ValueError("Goal mutation requires expected_version")
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
        if expected_version is None:
            raise ValueError("Goal mutation requires expected_version")
        return super().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def list(self, *, session_id=None, status=None):
        if session_id is not None:
            self.reference(session_id)
        return {key: value for key, value in super().list().items()
                if (session_id is None or value["scope"] in ({"type": "project"}, {"type": "session", "id": session_id}))
                and (status is None or value["status"] == status)}

    @workspace_locked
    def link_run(self, identifier, session_id, run_id, *, relation, expected_version):
        value = self.load(identifier)
        ref = {"session_id": session_id, "run_id": run_id, "relation": relation}
        refs = value.get("run_refs", [])
        return self.update(identifier, {"run_refs": refs if ref in refs else [*refs, ref]}, expected_version=expected_version)

    @workspace_locked
    def unlink_run(self, identifier, session_id, run_id, *, expected_version):
        value = self.load(identifier)
        return self.update(identifier, {"run_refs": [ref for ref in value.get("run_refs", [])
            if (ref["session_id"], ref["run_id"]) != (session_id, run_id)]}, expected_version=expected_version)

    def complete(self, identifier, *, expected_version):
        return self.update(identifier, {"status": "completed"}, expected_version=expected_version)

    def pause(self, identifier, *, expected_version):
        return self.update(identifier, {"status": "paused"}, expected_version=expected_version)

    def resume(self, identifier, *, expected_version):
        return self.update(identifier, {"status": "active"}, expected_version=expected_version)

    @workspace_locked
    def context_references(self, session_id, *, identifiers=None):
        records = self.list(session_id=session_id, status="active")
        return [{"goal_id": key, "version": revision_token(value), **{name: deepcopy(value[name])
                    for name in ("objective", "success_criteria", "progress", "next_actions") if name in value}}
                for key, value in records.items() if identifiers is None or key in identifiers]

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
