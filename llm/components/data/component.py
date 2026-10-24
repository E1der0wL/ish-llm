"""학습 자료·예제·임시 파일을 명시적으로 보관한다. 데이터의 의미는 Application이 정한다."""

import json
from pathlib import Path
import shutil

from llm.components.files import FileComponent, FileData, file_path
from llm.components.definitions import DefinitionComponent
from llm.components.file_manifest import FileManifest
from llm.core.models import new_id, now
from llm.core.editable_snapshot import EditableSnapshot
from .records import DataDefinition, DataQuarantineInspection, DataQuarantineReceipt
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import (
    async_method, atomic_json, check_revision, make_directory, remove_named_tree,
    revision_token, tree_snapshot,
)


class DataData(FileData):
    """Explicit quarantine needs current references, inactive owners and byte evidence."""

    UNBOUND_EXECUTION = None
    INCLUDE_DELETED_SESSIONS = True
    MISSING_EXECUTION = "Data quarantine requires bound execution references"
    UNFINISHED_RESUME = "Data quarantine cannot discard unfinished Resume references"

    def _quarantine_owner(self, identifier):
        project, component = self._current()
        self.references.require_unreferenced(project, self.name, identifier)
        if self._sessions is self.UNBOUND_EXECUTION or self._sessions.run_repository is self.UNBOUND_EXECUTION:
            raise RuntimeError(self.MISSING_EXECUTION)
        for session in self._sessions.list(project, include_deleted=self.INCLUDE_DELETED_SESSIONS):
            self._sessions.require_inactive(session)
            if self._sessions.run_repository.resume_references(session):
                raise ValueError(self.UNFINISHED_RESUME)
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
    directory = name
    capabilities = (name,)
    data_class = DataData
    record_name = "data.json"
    QUARANTINE_DIRECTORY = "quarantine"
    QUARANTINE_RECORD = "receipt.json"
    QUARANTINE_EVENT = "data.quarantined"
    QUARANTINE_DELETE_EVENT = "data.quarantine_deleted"
    COPY_LINKS = True
    INVALID_PACKAGE_ENTRIES = 'Data quarantine cannot discard unknown or missing package entries'
    MISSING_REVISION = 'Data quarantine requires expected_revision from inspection'
    QUARANTINED_PACKAGE = 'Data package is quarantined'
    MISSING_DECLARED_FILES = 'Data quarantine requires declared attachments in the original definition'
    ALREADY_QUARANTINED = 'Data package is already quarantined'
    MISSING_ATTACHMENTS = 'Data quarantine requires its existing attachment directory'
    COPY_MODE = 'xb'
    CHANGED_ATTACHMENTS = 'Data attachments changed during quarantine'
    INVALID_PRESERVED_TREE = 'Data quarantine requires its preserved tree and absent original attachments'
    INVENTORY_MISMATCH = 'Data quarantine inventory mismatch'
    MISSING_PRESERVED_BYTES = 'Data quarantine bytes changed or are missing'
    schema = DataDefinition.schema()

    def _quarantine_path(self, project, identifier):
        return self._checked(self._package(project, identifier) / self.QUARANTINE_DIRECTORY)

    def _require_package_entries(self, project, identifier, attachment_directory):
        entries = {self._checked(entry).name for entry in self._package(project, identifier).iterdir()}
        if entries != {self.record_name, attachment_directory}:
            raise ValueError(self.INVALID_PACKAGE_ENTRIES)

    @staticmethod
    def _require_revision(expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError(DataComponent.MISSING_REVISION)

    def _verify(self, project, identifier, value):
        if self._quarantine_path(project, identifier).exists():
            self.quarantine_snapshot(project, identifier)
            raise ValueError(self.QUARANTINED_PACKAGE)
        super()._verify(project, identifier, value)

    def inspect_quarantine(self, project, identifier):
        definition = self._definition(project, identifier)
        # The retained definition must independently prevent an empty-package read
        # even if the quarantine receipt and copied tree are subsequently lost.
        if not FileManifest(definition).optional:
            raise ValueError(self.MISSING_DECLARED_FILES)
        if self._quarantine_path(project, identifier).exists():
            raise ValueError(self.ALREADY_QUARANTINED)
        source = self._files(project, identifier)
        if not source.is_dir():
            raise ValueError(self.MISSING_ATTACHMENTS)
        self._require_package_entries(project, identifier, source.name)
        files = tree_snapshot(source)
        for name in files:
            file_path(name)
        value = DataQuarantineInspection.create(project.id, self.name, identifier, revision_token(definition), files)
        return DataQuarantineInspection.snapshot(value)

    def _copy_quarantine_file(self, source, destination):
        with self._stream(self._checked(Path(source))) as incoming, Path(destination).open(self.COPY_MODE) as outgoing:
            self._digest(incoming, outgoing)
        return destination

    def quarantine(self, project, identifier, *, expected_revision):
        self._require_revision(expected_revision)
        inspection = EditableSnapshot.read(self.inspect_quarantine(project, identifier)).data
        check_revision(inspection, expected_revision)
        source = self._files(project, identifier)
        destination = self._quarantine_path(project, identifier)
        make_directory(destination)
        copied = destination / source.name
        shutil.copytree(source, copied, symlinks=self.COPY_LINKS, copy_function=self._copy_quarantine_file)
        for path in (source, copied):
            if revision_token(tree_snapshot(path)) != revision_token(DataQuarantineInspection(inspection).files):
                raise ValueError(self.CHANGED_ATTACHMENTS)
        receipt = DataQuarantineReceipt.create(inspection, new_id(), now())
        atomic_json(destination / self.QUARANTINE_RECORD, receipt)
        remove_named_tree(self._package(project, identifier), source, source.name)
        return self.quarantine_snapshot(project, identifier)

    def quarantine_snapshot(self, project, identifier):
        definition = self._definition(project, identifier)
        root = self._quarantine_path(project, identifier)
        original = self._files(project, identifier)
        if not root.is_dir() or original.exists():
            raise ValueError(self.INVALID_PRESERVED_TREE)
        self._require_package_entries(project, identifier, root.name)
        if {entry.name for entry in root.iterdir()} != {self.QUARANTINE_RECORD, original.name}:
            raise ValueError(self.INVENTORY_MISMATCH)
        with self._stream(self._checked(root / self.QUARANTINE_RECORD)) as stream:
            value = json.load(stream)
        DataQuarantineReceipt.validate(value, project_id=project.id, component=self.name,
                                       identifier=identifier, definition=definition)
        files = self._checked(root / original.name)
        if not files.is_dir() or revision_token(tree_snapshot(files)) != revision_token(DataQuarantineReceipt(value).files):
            raise ValueError(self.MISSING_PRESERVED_BYTES)
        return DataQuarantineReceipt.snapshot(value)

    def delete_quarantined(self, project, identifier, *, expected_revision):
        self._require_revision(expected_revision)
        receipt = self.quarantine_snapshot(project, identifier)
        check_revision(EditableSnapshot.read(receipt).data, expected_revision)
        # The ordinary File delete remains strict. This owner proved its retained bytes above.
        DefinitionComponent.delete(self, project, identifier)
