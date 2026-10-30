"""Run-owned checkpoint files; Engine payload and record meaning have separate Owners.

The manifest and record digests remain linked to the Run's revision witness. All
I/O runs under the caller's StorageIO/workspace transaction and ownership lock.
"""

from llm.services.infrastructure.storage import record_exists, record_children

from copy import deepcopy
import hashlib
import json

from llm.components.base import validate_name
from llm.core.checkpoints import CheckpointOperation, CheckpointConversation, CheckpointStatus
from llm.core.checkpoint_records import (CheckpointManifest, CheckpointRecord, CheckpointDeclarations,
    CheckpointDeletion, CheckpointWrite, CheckpointStep)
from llm.services.infrastructure.storage import atomic_json, read_json, make_directory, revision_token


def checkpoint_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, **CheckpointRepository.DIGEST_JSON).encode(CheckpointRepository.ENCODING)).hexdigest()


def checkpoint_message_ids(checkpoint: dict) -> set[str]:
    """Conversation and direct/nested instruction references shared by resume and deletion."""
    from llm.services.runtime.steering import instruction_records
    references = CheckpointConversation.from_checkpoint(checkpoint)
    result = {*references.message_ids, references.input_message_id}
    for _, record in instruction_records(checkpoint):
        result.update(record.message_ids)
    return result


class CheckpointRepository:
    """Own files and atomic commit order; records own their declarations and validation."""

    DIRECTORY = "checkpoints"
    MANIFEST = "checkpoint.json"
    RECORDS = "records"
    SUFFIX = ".json"
    ENCODING = "utf-8"
    DIGEST_JSON = {"sort_keys": True, "ensure_ascii": False, "allow_nan": False}
    LINKS_FORBIDDEN = "Checkpoint paths cannot follow links"
    DELETED_FILES = "Deleted checkpoint files unexpectedly exist"
    UNDECLARED_DIRECTORIES = "Checkpoint directories are missing or undeclared"
    UNDECLARED_CHECKPOINT = "Checkpoint is not declared by its Run"
    MANIFEST_MISMATCH = "Checkpoint manifest revision mismatch"
    INVALID_ENTRIES = "Invalid checkpoint storage entries"
    UNDECLARED_RECORDS = "Checkpoint records are missing or undeclared"
    DELETED_WRITE = "Checkpoints were deleted; start a new Run"
    ALREADY_INITIALIZED = "Checkpoint already initialized"
    INVALID_BINDING = "Interaction checkpoint binding mismatch"
    DIFFERENT_INTERACTION = "Interaction event differs from persisted request"
    MISSING_INTERACTION = "Interaction event requires a persisted request"
    UNKNOWN_OPERATION = "Unknown checkpoint operation"
    DELETED_READ = "Checkpoints were explicitly deleted"

    def _checked(self, path):
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError(self.LINKS_FORBIDDEN)
        return path

    def _root(self, run, name):
        return self._checked(self.root_path(run) / validate_name(name))

    def root_path(self, run):
        return self._checked(run.paths.state / self.DIRECTORY)

    def deletion(self, run):
        """Read explicit retirement evidence; it can never authorize resume."""
        record = CheckpointDeletion.read(run.metadata)
        if record is None:
            return None
        record.require_retired(run)
        if record_exists(self._checked(run.paths.state / self.DIRECTORY)):
            raise ValueError(self.DELETED_FILES)
        return record

    def delete(self, run, *, names, interactions, revision):
        """Commit physical deletion and its permanent marker in the caller's transaction."""
        from llm.core.models import now
        from llm.services.infrastructure.storage import remove_named_tree
        record = CheckpointDeletion(now(), revision, tuple(names), tuple(interactions))
        root = self._checked(run.paths.state / self.DIRECTORY)
        if record_exists(root):
            remove_named_tree(root.parent, root, self.DIRECTORY)
        CheckpointDeclarations.remove(run.metadata)
        record.store(run.metadata)

    def names(self, run):
        """Run declaration and owner revision distinguish absent from lost state."""
        names = CheckpointDeclarations.read(run.metadata).names
        root = self._checked(run.paths.state / self.DIRECTORY)
        actual = {self._checked(path).name for path in record_children(root)} if record_exists(root) else set()
        if actual != set(names):
            raise ValueError(self.UNDECLARED_DIRECTORIES)
        return list(names)

    def _stored(self, run, name):
        names = self.names(run)
        root = self._root(run, name)
        raw = read_json(self._checked(root / self.MANIFEST))
        if name not in names:
            raise ValueError(self.UNDECLARED_CHECKPOINT)
        data = CheckpointManifest.from_dict(raw)
        data.require_owner(run)
        if revision_token(raw) != CheckpointDeclarations.read(run.metadata).revisions[name]:
            raise ValueError(self.MANIFEST_MISMATCH)
        directory = self._checked(root / self.RECORDS)
        if {self._checked(path).name for path in record_children(root)} != {self.MANIFEST, self.RECORDS}:
            raise ValueError(self.INVALID_ENTRIES)
        expected = {self._filename(key) for key in data.record_revisions}
        if {self._checked(path).name for path in record_children(directory)} != expected:
            raise ValueError(self.UNDECLARED_RECORDS)
        records = deepcopy(data.records)
        for key, revision in data.record_revisions.items():
            raw_record = read_json(self._checked(directory / self._filename(key)))
            record = CheckpointRecord.from_dict(raw_record)
            if record.key != key or revision_token(raw_record) != revision:
                raise ValueError(CheckpointRecord.INVALID)
            records[key] = record.value
        return data, records

    @classmethod
    def _filename(cls, key):
        return hashlib.sha256(key.encode(cls.ENCODING)).hexdigest() + cls.SUFFIX

    def _manifest(self, run, name, data):
        value = data.to_dict()
        atomic_json(self._root(run, name) / self.MANIFEST, value)
        CheckpointDeclarations.record_revision(run.metadata, name, revision_token(value))

    def record(self, run, event) -> None:
        if self.deletion(run) is not None:
            raise ValueError(self.DELETED_WRITE)
        write = CheckpointWrite(event.metadata)
        name = write.name
        root = self._root(run, name)
        if write.operation == CheckpointOperation.INITIALIZE:
            names = self.names(run)
            if name in names or record_exists(root):
                raise ValueError(self.ALREADY_INITIALIZED)
            manifest = write.initial_manifest(run)
            make_directory(self._checked(root / self.RECORDS))
            self._manifest(run, name, manifest)
            CheckpointDeclarations.declare(run.metadata, name)
        elif write.operation == CheckpointOperation.RECORD:
            stored, _ = self._stored(run, name)
            item = write.record
            step = CheckpointStep(item.value)
            request = step.optional_interaction
            if request is not None:
                binding = request.checkpoint_binding
                if binding.checkpoint != name or binding.key != item.key or step.status != CheckpointStatus.WAITING:
                    raise ValueError(self.INVALID_BINDING)
                if event.interaction is not None and event.interaction.to_dict() != request.to_dict():
                    raise ValueError(self.DIFFERENT_INTERACTION)
            elif event.interaction is not None:
                raise ValueError(self.MISSING_INTERACTION)
            value = item.to_dict()
            atomic_json(self._checked(root / self.RECORDS / self._filename(item.key)), value)
            stored = stored.with_revision(item.key, revision_token(value))
            self._manifest(run, name, stored)
        else:
            raise ValueError(self.UNKNOWN_OPERATION)

    def load(self, run, name: str) -> dict:
        if self.deletion(run) is not None:
            raise ValueError(self.DELETED_READ)
        data, records = self._stored(run, name)
        return data.resolved(records).to_dict()
