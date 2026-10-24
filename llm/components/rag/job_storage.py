"""RAG-owned Job inventory and persistent worker lease identity."""

from .status import RAGJobStatus
from .job_records import JobRecord
from .chunk_records import RAGDocument, RAGDocumentInput, SplitDocument
import fcntl
import os
import re
import stat

from llm.components.base import validate_name
from llm.services.infrastructure.storage import (
    DeletionJournal, atomic_bytes, atomic_json, make_directory, read_json, remove_named_tree, revision_token)


class JobInventory:
    SCHEMA_VERSION = 1

    class Field:
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        COMPONENT = "component"
        JOBS = "jobs"

    FIELDS = frozenset((Field.SCHEMA_VERSION, Field.PROJECT_ID, Field.COMPONENT, Field.JOBS))

    @classmethod
    def create(cls, project_id, component):
        return {cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION,
                cls.Field.PROJECT_ID: project_id, cls.Field.COMPONENT: component, cls.Field.JOBS: {}}


class JobInventoryEntry:
    class Field:
        REVISION = "revision"
        CHECKPOINTS = "checkpoints"

    FIELDS = frozenset((Field.REVISION, Field.CHECKPOINTS))

    @classmethod
    def create(cls, revision):
        return {cls.Field.REVISION: revision, cls.Field.CHECKPOINTS: {}}


class JobBatchReceipt:
    class Field:
        KEY = "key"
        SIGNATURE = "signature"
        VALUE = "value"

    FIELDS = frozenset((Field.KEY, Field.SIGNATURE, Field.VALUE))

    @classmethod
    def create(cls, key, signature, value):
        return {cls.Field.KEY: key, cls.Field.SIGNATURE: signature, cls.Field.VALUE: value}


class JobDocumentBatch:
    DOCUMENTS = "documents"
    INPUT = "input"

    @classmethod
    def signature_payload(cls, source, configuration):
        return {cls.INPUT: source, JobRecord.Field.CONFIGURATION: configuration}


class JobStorage:
    ERROR_CANNOT_ADOPT_EXISTING_STORAGE = 'Cannot adopt existing RAG Job storage'
    ERROR_EVIDENCE_MUST_BE_A_REGULAR_FILE = 'RAG Job evidence must be a regular file'
    ERROR_WORKER_LEASE_IS_MISSING_OR_NOT_A_REGULAR_FILE = 'RAG Job worker lease is missing or not a regular file'
    ERROR_INVALID_MANIFEST = 'Invalid RAG Job manifest'
    ERROR_DIRECTORY_IS_MISSING = 'RAG Job directory is missing'
    ERROR_ARE_MISSING_OR_UNDECLARED = 'RAG Jobs are missing or undeclared'
    ERROR_INPUT_CHANGED = 'RAG Job input changed'
    ERROR_INPUT_COUNT_CHANGED = 'RAG Job input count changed'
    ERROR_REQUIRED_INPUT_IS_MISSING = 'Required RAG Job input is missing'
    ERROR_CHECKPOINT_MUST_BE_AN_OBJECT = 'RAG Job checkpoint must be an object'
    ERROR_INVALID_BATCH_RECEIPT = 'Invalid RAG Job batch receipt'
    ERROR_PREPARED_RAG_DOCUMENT_REQUIRES_ITS_JOB_ATTEMPT = 'Prepared RAG document requires its Job attempt'
    ERROR_PREPARED_RAG_DOCUMENT_COUNT_CHANGED = 'Prepared RAG document count changed'
    ERROR_DOES_NOT_EXIST = 'RAG Job does not exist'
    ERROR_UNDECLARED_CHECKPOINT = 'Undeclared RAG Job checkpoint'
    ERROR_REQUIRED_CHECKPOINT_IS_MISSING = 'Required RAG Job checkpoint is missing'
    ERROR_CHECKPOINTS_REQUIRE_THEIR_STORED_ATTEMPT_EVIDENCE = 'RAG Job checkpoints require their stored attempt evidence'
    ERROR_ONLY_COMPLETED_JOB_PREPARATION_COPIES_CAN_BE_CLEARED = 'Only completed Job preparation copies can be cleared'
    ERROR_ALREADY_EXISTS = 'RAG Job already exists'
    ERROR_WORKER_LEASE_IDENTITY_CHANGED = 'RAG Job worker lease identity changed'
    ERROR_MUST_BE_DIRECTORIES = 'RAG Jobs must be directories'
    ERROR_INVALID_INVENTORY_ENTRY = 'Invalid RAG Job inventory entry'
    ERROR_REVISION_CHANGED = 'RAG Job revision changed'
    ERROR_INVALID_BATCH_INPUT = 'Invalid RAG Job batch input'
    ERROR_INPUT_IDENTITY_CHANGED = 'RAG Job input identity changed'
    ERROR_INVALID_CHECKPOINT_PATH = 'Invalid RAG Job checkpoint path'
    ERROR_SINGLE_DOCUMENT_HAS_A_DOCUMENT_BATCH = 'Single-document RAG Job has a document batch'
    ERROR_PREPARED_RAG_DOCUMENT_DISAGREES_WITH_ITS_ORIGINAL_JOB_INPUT_OR_ATTEMPT = 'Prepared RAG document disagrees with its original Job input or attempt'
    ERROR_PREPARED_RAG_DOCUMENT_METADATA_DIFFERS_FROM_ITS_ORIGINAL_JSON_INPUT = 'Prepared RAG document metadata differs from its original JSON input'
    ERROR_PREPARED_RAG_DOCUMENT_DESCRIPTION_DIFFERS_FROM_ITS_INPUT = 'Prepared RAG document description differs from its input'
    ERROR_PREPARED_RAG_DOCUMENT_CONFIGURATION_DIFFERS_FROM_ITS_JOB_ATTEMPT = 'Prepared RAG document configuration differs from its Job attempt'
    ERROR_CHECKPOINT_REVISION_CHANGED = 'RAG Job checkpoint revision changed'
    ERROR_WORKER_LEASE_CHANGED_DURING_ACQUISITION = 'RAG Job worker lease changed during acquisition'
    ERROR_INDEXING_WORKER_IS_STILL_ALIVE = 'Indexing worker is still alive'
    ERROR_INVALID_CHECKPOINT_REVISION = 'Invalid RAG Job checkpoint revision'
    ERROR_UNDECLARED_PREPARED_RAG_DOCUMENT_POSITION = 'Undeclared prepared RAG document position'
    ERROR_PREPARED_RAG_DOCUMENT_BATCH_SIGNATURE_CHANGED = 'Prepared RAG document batch signature changed'
    ERROR_INVALID_PREPARED_RAG_BATCH_ENVELOPE = 'Invalid prepared RAG batch envelope'
    ERROR_UNDECLARED_CONTENTS = 'Undeclared RAG Job contents'

    ABSENT = None
    RECORD = "job.json"
    INPUT = "input.json"
    CHECKPOINT_SEPARATOR = "/"
    CHECKPOINT_SUFFIX = ".json"
    DIGEST_PATTERN = r"[a-f0-9]{64}"
    DOCUMENT_BATCH_PATTERN = r"batches/document_(0|[1-9][0-9]*)\.json"
    DOCUMENT_POSITION_GROUP = 1
    FIRST_INPUT = 0
    CHECKPOINT_VALUES = 1
    INVENTORY_MANIFEST = 0
    LEASE_ENCODING = "utf-8"
    LEASE_READ_MODE = "rb"
    LEASE_UPDATE_MODE = "r+b"
    LEASE_EXTRA_BYTE = 1
    ACQUIRE_LEASE = False
    MANIFEST = "jobs.manifest.json"
    DIRECTORY = "jobs"
    LEASE = "worker.lock"
    PREPARED = "prepared.json"
    BATCHES = "batches"

    def __init__(self, project, component):
        self.project_id, self.component = project.id, component.name
        self.validate_document = component.validate_document
        self.validate_input_document = component.validate_input
        self.validate_batch_checkpoint = component.validate_batch_checkpoint
        self.checked = component._checked
        root = self.checked(component.root(project))
        self.directory = self.checked(root / self.DIRECTORY)
        self.manifest = self.checked(root / self.MANIFEST)

    def path(self, identifier):
        return self.checked(self.directory / validate_name(identifier))

    def initialize(self):
        if self.manifest.exists() or self.directory.exists():
            raise ValueError(JobStorage.ERROR_CANNOT_ADOPT_EXISTING_STORAGE)
        make_directory(self.directory)
        atomic_json(self.manifest, JobInventory.create(self.project_id, self.component))

    def _json(self, path):
        path = self.checked(path)
        if not path.is_file():
            raise ValueError(JobStorage.ERROR_EVIDENCE_MUST_BE_A_REGULAR_FILE)
        return read_json(path)

    def _lease_path(self, identifier):
        path = self.checked(self.path(identifier) / self.LEASE)
        if not path.is_file():
            raise ValueError(JobStorage.ERROR_WORKER_LEASE_IS_MISSING_OR_NOT_A_REGULAR_FILE)
        expected = identifier.encode(JobStorage.LEASE_ENCODING)
        with path.open(JobStorage.LEASE_READ_MODE) as stream:
            if stream.read(len(expected) + JobStorage.LEASE_EXTRA_BYTE) != expected:
                raise ValueError(JobStorage.ERROR_WORKER_LEASE_IDENTITY_CHANGED)
        return path

    def inventory(self):
        manifest = self._json(self.manifest)
        if (not isinstance(manifest, dict)
                or manifest.keys() != JobInventory.FIELDS
                or type(manifest[JobInventory.Field.SCHEMA_VERSION]) is not int
                or manifest[JobInventory.Field.SCHEMA_VERSION] != JobInventory.SCHEMA_VERSION
                or manifest[JobInventory.Field.PROJECT_ID] != self.project_id
                or manifest[JobInventory.Field.COMPONENT] != self.component or not isinstance(manifest[JobInventory.Field.JOBS], dict)):
            raise ValueError(JobStorage.ERROR_INVALID_MANIFEST)
        if not self.directory.is_dir():
            raise ValueError(JobStorage.ERROR_DIRECTORY_IS_MISSING)
        actual = set()
        for path in self.directory.iterdir():
            self.checked(path)
            if path.name == DeletionJournal.DIRECTORY:
                continue  # Existing explicit deletion intents have their own recovery contract.
            if not path.is_dir():
                raise ValueError(JobStorage.ERROR_MUST_BE_DIRECTORIES)
            actual.add(validate_name(path.name))
        if actual != manifest[JobInventory.Field.JOBS].keys():
            raise ValueError(JobStorage.ERROR_ARE_MISSING_OR_UNDECLARED)
        records = {}
        for identifier, entry in manifest[JobInventory.Field.JOBS].items():
            if (not isinstance(entry, dict) or entry.keys() != JobInventoryEntry.FIELDS
                    or not isinstance(entry[JobInventoryEntry.Field.CHECKPOINTS], dict)):
                raise ValueError(JobStorage.ERROR_INVALID_INVENTORY_ENTRY)
            for key, revision in entry[JobInventoryEntry.Field.CHECKPOINTS].items():
                self.checkpoint_path(identifier, key)
                if not isinstance(revision, str) or re.fullmatch(JobStorage.DIGEST_PATTERN, revision) is JobStorage.ABSENT:
                    raise ValueError(JobStorage.ERROR_INVALID_CHECKPOINT_REVISION)
            root = self.path(identifier)
            job = JobRecord.validate(self._json(root / JobStorage.RECORD), identifier)
            if entry[JobInventoryEntry.Field.CHECKPOINTS] and not JobRecord.ATTEMPT_FIELDS <= job.keys():
                raise ValueError(JobStorage.ERROR_CHECKPOINTS_REQUIRE_THEIR_STORED_ATTEMPT_EVIDENCE)
            if entry[JobInventoryEntry.Field.REVISION] != revision_token(job):
                raise ValueError(JobStorage.ERROR_REVISION_CHANGED)
            self._lease_path(identifier)
            records[identifier] = job
        return manifest, records

    def validate_sources(self):
        """Backup/clone require input proof; resume diagnostics inspect missing inputs."""
        manifest, records = self.inventory()
        for identifier, job in records.items():
            self.read_input(identifier, job)
            self.checkpoints(identifier)
        return manifest, records

    def checkpoint_declarations(self, identifier):
        return self.inventory()[self.INVENTORY_MANIFEST][JobInventory.Field.JOBS][identifier][JobInventoryEntry.Field.CHECKPOINTS]

    def validate_input(self, job, payload):
        if revision_token(payload) != job[JobRecord.Field.INPUT_DIGEST]:
            raise ValueError(JobStorage.ERROR_INPUT_CHANGED)
        if JobRecord.Field.DOCUMENT_IDS in job:
            if (not isinstance(payload, dict) or payload.keys() != {JobDocumentBatch.DOCUMENTS}
                    or not isinstance(payload[JobDocumentBatch.DOCUMENTS], list)):
                raise ValueError(JobStorage.ERROR_INVALID_BATCH_INPUT)
            inputs, expected = payload[JobDocumentBatch.DOCUMENTS], job[JobRecord.Field.DOCUMENT_IDS]
        else:
            inputs, expected = [payload], [job[JobRecord.Field.DOCUMENT_ID]]
        if len(inputs) != len(expected):
            raise ValueError(JobStorage.ERROR_INPUT_COUNT_CHANGED)
        for source, identifier in zip(inputs, expected):
            self.validate_input_document(source)
            if source[RAGDocumentInput.Field.IDENTIFIER] != identifier:
                raise ValueError(JobStorage.ERROR_INPUT_IDENTITY_CHANGED)
        return payload

    def read_input(self, identifier, job):
        path = self.checked(self.path(identifier) / JobStorage.INPUT)
        if not path.exists():
            raise FileNotFoundError(JobStorage.ERROR_REQUIRED_INPUT_IS_MISSING)
        return self.validate_input(job, self._json(path))

    def checkpoint_path(self, identifier, key):
        if key != self.PREPARED:
            prefix = self.BATCHES + JobStorage.CHECKPOINT_SEPARATOR
            if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(JobStorage.CHECKPOINT_SUFFIX):
                raise ValueError(JobStorage.ERROR_INVALID_CHECKPOINT_PATH)
            validate_name(key[len(prefix):-len(JobStorage.CHECKPOINT_SUFFIX)])
        return self.checked(self.path(identifier) / key)

    def validate_checkpoint(self, identifier, job, key, value):
        if not isinstance(value, dict):
            raise ValueError(JobStorage.ERROR_CHECKPOINT_MUST_BE_AN_OBJECT)
        if key != self.PREPARED and (value.keys() != JobBatchReceipt.FIELDS or value[JobBatchReceipt.Field.KEY] != key
                or not isinstance(value[JobBatchReceipt.Field.SIGNATURE], str) or not value[JobBatchReceipt.Field.SIGNATURE]
                or value[JobBatchReceipt.Field.VALUE] is JobStorage.ABSENT):
            raise ValueError(JobStorage.ERROR_INVALID_BATCH_RECEIPT)
        document_batch = re.fullmatch(JobStorage.DOCUMENT_BATCH_PATTERN, key)
        payload = self.read_input(identifier, job)
        if key != self.PREPARED and document_batch is JobStorage.ABSENT:
            self.validate_batch_checkpoint(job, key, value, payload)
            return
        if not JobRecord.ATTEMPT_FIELDS <= job.keys():
            raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_REQUIRES_ITS_JOB_ATTEMPT)
        if JobRecord.Field.DOCUMENT_IDS in job:
            inputs, expected = payload[JobDocumentBatch.DOCUMENTS], job[JobRecord.Field.DOCUMENT_IDS]
            if document_batch is not JobStorage.ABSENT:
                position = int(document_batch[JobStorage.DOCUMENT_POSITION_GROUP])
                if position >= len(inputs):
                    raise ValueError(JobStorage.ERROR_UNDECLARED_PREPARED_RAG_DOCUMENT_POSITION)
                inputs, expected = [inputs[position]], [expected[position]]
                documents = [value[JobBatchReceipt.Field.VALUE]]
                if value[JobBatchReceipt.Field.SIGNATURE] != revision_token(JobDocumentBatch.signature_payload(inputs[JobStorage.FIRST_INPUT], job[JobRecord.Field.CONFIGURATION])):
                    raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_BATCH_SIGNATURE_CHANGED)
            else:
                if value.keys() != {JobDocumentBatch.DOCUMENTS} or not isinstance(value[JobDocumentBatch.DOCUMENTS], list):
                    raise ValueError(JobStorage.ERROR_INVALID_PREPARED_RAG_BATCH_ENVELOPE)
                documents = value[JobDocumentBatch.DOCUMENTS]
        else:
            if document_batch is not JobStorage.ABSENT:
                raise ValueError(JobStorage.ERROR_SINGLE_DOCUMENT_HAS_A_DOCUMENT_BATCH)
            inputs, expected, documents = [payload], [job[JobRecord.Field.DOCUMENT_ID]], [value]
        if len(documents) != len(expected):
            raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_COUNT_CHANGED)
        for source, expected_id, document in zip(inputs, expected, documents):
            self.validate_document(document)
            if (document[RAGDocument.Field.ID] != expected_id
                    or document[RAGDocument.Field.REVISION] != RAGDocument.INITIAL_REVISION
                    or any(document[field] != source[field] for field in (RAGDocument.Field.TITLE, SplitDocument.Field.CONTENT))):
                raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_DISAGREES_WITH_ITS_ORIGINAL_JOB_INPUT_OR_ATTEMPT)
            if revision_token(document[RAGDocument.Field.METADATA]) != revision_token(source[RAGDocument.Field.METADATA]):
                raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_METADATA_DIFFERS_FROM_ITS_ORIGINAL_JSON_INPUT)
            if document.get(RAGDocument.Field.DESCRIPTION) != source.get(RAGDocument.Field.DESCRIPTION):
                raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_DESCRIPTION_DIFFERS_FROM_ITS_INPUT)
            if document[RAGDocument.Field.PREPARATION_CONFIGURATION] != job[JobRecord.Field.CONFIGURATION]:
                raise ValueError(JobStorage.ERROR_PREPARED_RAG_DOCUMENT_CONFIGURATION_DIFFERS_FROM_ITS_JOB_ATTEMPT)

    def checkpoints(self, identifier):
        manifest, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError(JobStorage.ERROR_DOES_NOT_EXIST)
        declared = manifest[JobInventory.Field.JOBS][identifier][JobInventoryEntry.Field.CHECKPOINTS]
        root = self.path(identifier)
        actual = set()
        for path in root.iterdir():
            self.checked(path)
            if path.name in (JobStorage.RECORD, JobStorage.INPUT, self.LEASE):
                continue
            if path.name == self.BATCHES and path.is_dir():
                for entry in path.iterdir():
                    key = self.BATCHES + JobStorage.CHECKPOINT_SEPARATOR + entry.name
                    self.checkpoint_path(identifier, key)
                    actual.add(key)
            elif path.name == self.PREPARED:
                actual.add(self.PREPARED)
            else:
                raise ValueError(JobStorage.ERROR_UNDECLARED_CONTENTS)
        if actual - declared.keys():
            raise ValueError(JobStorage.ERROR_UNDECLARED_CHECKPOINT)
        if declared.keys() - actual:
            raise FileNotFoundError(JobStorage.ERROR_REQUIRED_CHECKPOINT_IS_MISSING)
        values = {}
        for key, revision in declared.items():
            value = self._json(self.checkpoint_path(identifier, key))
            self.validate_checkpoint(identifier, records[identifier], key, value)
            if revision_token(value) != revision:
                raise ValueError(JobStorage.ERROR_CHECKPOINT_REVISION_CHANGED)
            values[key] = value
        return manifest, values

    def read_checkpoint(self, identifier, key):
        self.checkpoint_path(identifier, key)
        return self.checkpoints(identifier)[JobStorage.CHECKPOINT_VALUES].get(key)

    def write_checkpoint(self, identifier, key, value):
        manifest, _ = self.checkpoints(identifier)
        path = self.checkpoint_path(identifier, key)
        job = JobRecord.validate(self._json(self.path(identifier) / JobStorage.RECORD), identifier)
        if not JobRecord.ATTEMPT_FIELDS <= job.keys():
            raise ValueError(JobStorage.ERROR_CHECKPOINTS_REQUIRE_THEIR_STORED_ATTEMPT_EVIDENCE)
        self.validate_checkpoint(identifier, job, key, value)
        atomic_json(path, value)
        manifest[JobInventory.Field.JOBS][identifier][JobInventoryEntry.Field.CHECKPOINTS][key] = revision_token(value)
        atomic_json(self.manifest, manifest)

    def clear_checkpoints(self, identifier):
        _, records = self.inventory()
        if records[identifier][JobRecord.Field.STATUS] != RAGJobStatus.COMPLETED.value:
            raise ValueError(JobStorage.ERROR_ONLY_COMPLETED_JOB_PREPARATION_COPIES_CAN_BE_CLEARED)
        manifest, values = self.checkpoints(identifier)
        from llm.services.infrastructure.storage import unlink_file
        if self.PREPARED in values:
            unlink_file(self.checkpoint_path(identifier, self.PREPARED))
        batches = self.checked(self.path(identifier) / self.BATCHES)
        if batches.exists():
            remove_named_tree(batches.parent, batches, self.BATCHES)
        manifest[JobInventory.Field.JOBS][identifier][JobInventoryEntry.Field.CHECKPOINTS] = {}
        atomic_json(self.manifest, manifest)

    def write_batch(self, identifier, key, signature, value):
        self.write_checkpoint(identifier, key, JobBatchReceipt.create(key, signature, value))

    def create(self, job, payload):
        manifest, records = self.inventory()
        identifier = validate_name(job[JobRecord.Field.ID])
        JobRecord.validate(job, identifier)
        if identifier in records:
            raise FileExistsError(JobStorage.ERROR_ALREADY_EXISTS)
        self.validate_input(job, payload)
        root = self.path(identifier)
        atomic_json(root / JobStorage.INPUT, payload)
        atomic_bytes(root / self.LEASE, identifier.encode(JobStorage.LEASE_ENCODING))
        manifest[JobInventory.Field.JOBS][identifier] = JobInventoryEntry.create(revision_token(job))
        self._write(job, manifest)

    def _write(self, job, manifest):
        identifier = job[JobRecord.Field.ID]
        atomic_json(self.path(identifier) / JobStorage.RECORD, job)
        manifest[JobInventory.Field.JOBS][identifier][JobInventoryEntry.Field.REVISION] = revision_token(job)
        atomic_json(self.manifest, manifest)

    def save(self, job):
        manifest, records = self.inventory()
        identifier = validate_name(job[JobRecord.Field.ID])
        JobRecord.validate(job, identifier)
        if identifier not in records:
            raise FileNotFoundError(JobStorage.ERROR_DOES_NOT_EXIST)
        previous = records[identifier]
        JobRecord.require_update(job, previous)
        self._write(job, manifest)

    def delete(self, identifier):
        manifest, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError(JobStorage.ERROR_DOES_NOT_EXIST)
        remove_named_tree(self.directory, self.path(identifier), identifier)
        del manifest[JobInventory.Field.JOBS][identifier]
        atomic_json(self.manifest, manifest)

    def lease(self, identifier, *, probe=ACQUIRE_LEASE):
        _, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError(JobStorage.ERROR_DOES_NOT_EXIST)
        path = self._lease_path(identifier)
        stream = os.fdopen(os.open(path, os.O_RDWR | os.O_NOFOLLOW), JobStorage.LEASE_UPDATE_MODE)
        try:
            opened, current = os.fstat(stream.fileno()), self.checked(path).stat()
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                    or stream.read(len(identifier.encode(JobStorage.LEASE_ENCODING)) + JobStorage.LEASE_EXTRA_BYTE) != identifier.encode(JobStorage.LEASE_ENCODING)):
                raise ValueError(JobStorage.ERROR_WORKER_LEASE_CHANGED_DURING_ACQUISITION)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            if probe:
                return JobStorage.ABSENT
            raise ValueError(JobStorage.ERROR_INDEXING_WORKER_IS_STILL_ALIVE) from JobStorage.ABSENT
        except BaseException:
            stream.close()
            raise
        return stream
