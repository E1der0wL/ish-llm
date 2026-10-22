"""학습 자료·예제·임시 파일을 명시적으로 보관한다. 데이터의 의미는 Application이 정한다."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil

from llm.components.files import FileComponent, FileData, FILE_MANIFEST, file_path
from llm.components.definitions import DefinitionComponent
from llm.core.models import new_id, now
from llm.core.schema import object_schema, metadata_schema
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import (
    async_method, atomic_json, check_revision, make_directory, remove_named_tree,
    revision_token, tree_snapshot,
)


class DataQuarantineReceipt:
    """Data attachment quarantine evidence has its own version, independent of package inventory."""

    SCHEMA_VERSION = 1
    STATUS = "quarantined"
    FIELDS = frozenset(("schema_version", "status", "id", "project_id", "component",
                        "identifier", "record_revision", "files", "created_at"))

    @classmethod
    def validate(cls, value, *, project_id, component, identifier, definition):
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or type(value["schema_version"]) is not int or value["schema_version"] != cls.SCHEMA_VERSION
                or value["status"] != cls.STATUS
                or not isinstance(value["id"], str) or not value["id"]
                or value["project_id"] != project_id or value["component"] != component
                or value["identifier"] != identifier
                or value["record_revision"] != revision_token(definition)
                or not isinstance(value["files"], dict) or not isinstance(value["created_at"], str)):
            raise ValueError("Invalid Data quarantine receipt")
        if datetime.fromisoformat(value["created_at"]).utcoffset() is None:
            raise ValueError("Data quarantine timestamp requires a timezone")


class DataData(FileData):
    """Explicit quarantine needs current references, inactive owners and byte evidence."""

    def _quarantine_owner(self, identifier):
        project, component = self._current()
        self.references.require_unreferenced(project, self.name, identifier)
        if self._sessions is None or self._sessions.run_repository is None:
            raise RuntimeError("Data quarantine requires bound execution references")
        for session in self._sessions.list(project, include_deleted=True):
            self._sessions.require_inactive(session)
            if self._sessions.run_repository.resume_references(session):
                raise ValueError("Data quarantine cannot discard unfinished Resume references")
        return project, component

    @workspace_locked
    def inspect_quarantine(self, identifier):
        project, component = self._current()
        return component.inspect_quarantine(project, identifier)

    @workspace_locked
    def quarantine(self, identifier, *, expected_revision):
        project, component = self._quarantine_owner(identifier)
        result = component.quarantine(project, identifier, expected_revision=expected_revision)
        log_event(project.paths.logs, component.QUARANTINE_EVENT, entity_id=identifier)
        return result

    @workspace_locked
    def quarantine_snapshot(self, identifier):
        project, component = self._current()
        return component.quarantine_snapshot(project, identifier)

    @workspace_locked
    def delete_quarantined(self, identifier, *, expected_revision):
        project, component = self._quarantine_owner(identifier)
        component.delete_quarantined(project, identifier, expected_revision=expected_revision)
        log_event(project.paths.logs, component.QUARANTINE_DELETE_EVENT, entity_id=identifier)

    ainspect_quarantine = async_method(inspect_quarantine)
    aquarantine = async_method(quarantine)
    aquarantine_snapshot = async_method(quarantine_snapshot)
    adelete_quarantined = async_method(delete_quarantined)


class DataComponent(FileComponent):
    """패키지 안의 바이너리/텍스트 파일을 소유한다. 실행 권한이나 자동 보관 정책은 없다."""

    name = "data"
    directory = "data"
    capabilities = ("data",)
    data_class = DataData
    record_name = "data.json"
    QUARANTINE_DIRECTORY = "quarantine"
    QUARANTINE_RECORD = "receipt.json"
    QUARANTINE_EVENT = "data.quarantined"
    QUARANTINE_DELETE_EVENT = "data.quarantine_deleted"
    schema = object_schema({
        "title": {"type": "string"},
        "description": {"type": "string"},
        "metadata": metadata_schema(),
        "files": FILE_MANIFEST,
    })

    def _quarantine_path(self, project, identifier):
        return self._checked(self._package(project, identifier) / self.QUARANTINE_DIRECTORY)

    def _require_package_entries(self, project, identifier, attachment_directory):
        entries = {self._checked(entry).name for entry in self._package(project, identifier).iterdir()}
        if entries != {self.record_name, attachment_directory}:
            raise ValueError("Data quarantine cannot discard unknown or missing package entries")

    @staticmethod
    def _require_revision(expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError("Data quarantine requires expected_revision from inspection")

    def _verify(self, project, identifier, value):
        if self._quarantine_path(project, identifier).exists():
            self.quarantine_snapshot(project, identifier)
            raise ValueError("Data package is quarantined")
        super()._verify(project, identifier, value)

    def inspect_quarantine(self, project, identifier):
        definition = self._definition(project, identifier)
        # The retained definition must independently prevent an empty-package read
        # even if the quarantine receipt and copied tree are subsequently lost.
        if not definition.get("files"):
            raise ValueError("Data quarantine requires declared attachments in the original definition")
        if self._quarantine_path(project, identifier).exists():
            raise ValueError("Data package is already quarantined")
        source = self._files(project, identifier)
        if not source.is_dir():
            raise ValueError("Data quarantine requires its existing attachment directory")
        self._require_package_entries(project, identifier, source.name)
        files = tree_snapshot(source)
        for name in files:
            file_path(name)
        value = {"project_id": project.id, "component": self.name, "identifier": identifier,
                 "record_revision": revision_token(definition), "files": files}
        return {"data": value, "version": revision_token(value)}

    def _copy_quarantine_file(self, source, destination):
        with self._stream(self._checked(Path(source))) as incoming, Path(destination).open("xb") as outgoing:
            self._digest(incoming, outgoing)
        return destination

    def quarantine(self, project, identifier, *, expected_revision):
        self._require_revision(expected_revision)
        inspection = self.inspect_quarantine(project, identifier)["data"]
        check_revision(inspection, expected_revision)
        source = self._files(project, identifier)
        destination = self._quarantine_path(project, identifier)
        make_directory(destination)
        copied = destination / source.name
        shutil.copytree(source, copied, symlinks=True, copy_function=self._copy_quarantine_file)
        for path in (source, copied):
            if revision_token(tree_snapshot(path)) != revision_token(inspection["files"]):
                raise ValueError("Data attachments changed during quarantine")
        receipt = {**inspection, "schema_version": DataQuarantineReceipt.SCHEMA_VERSION,
                   "status": DataQuarantineReceipt.STATUS, "id": new_id(), "created_at": now()}
        atomic_json(destination / self.QUARANTINE_RECORD, receipt)
        remove_named_tree(self._package(project, identifier), source, source.name)
        return self.quarantine_snapshot(project, identifier)

    def quarantine_snapshot(self, project, identifier):
        definition = self._definition(project, identifier)
        root = self._quarantine_path(project, identifier)
        original = self._files(project, identifier)
        if not root.is_dir() or original.exists():
            raise ValueError("Data quarantine requires its preserved tree and absent original attachments")
        self._require_package_entries(project, identifier, root.name)
        if {entry.name for entry in root.iterdir()} != {self.QUARANTINE_RECORD, original.name}:
            raise ValueError("Data quarantine inventory mismatch")
        with self._stream(self._checked(root / self.QUARANTINE_RECORD)) as stream:
            value = json.load(stream)
        DataQuarantineReceipt.validate(value, project_id=project.id, component=self.name,
                                       identifier=identifier, definition=definition)
        files = self._checked(root / original.name)
        if not files.is_dir() or revision_token(tree_snapshot(files)) != revision_token(value["files"]):
            raise ValueError("Data quarantine bytes changed or are missing")
        return {"data": deepcopy(value), "version": revision_token(value)}

    def delete_quarantined(self, project, identifier, *, expected_revision):
        self._require_revision(expected_revision)
        receipt = self.quarantine_snapshot(project, identifier)
        check_revision(receipt["data"], expected_revision)
        # The ordinary File delete remains strict. This owner proved its retained bytes above.
        DefinitionComponent.delete(self, project, identifier)
