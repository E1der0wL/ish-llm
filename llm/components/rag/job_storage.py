"""RAG-owned Job inventory and persistent worker lease identity."""

from datetime import datetime
from dataclasses import fields
import fcntl
import os
import re
import stat

from llm.components.base import validate_name
from llm.core.checkpoints import ResumeAbandonment
from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
from llm.services.infrastructure.storage import (
    atomic_bytes, atomic_json, make_directory, read_json, remove_named_tree, revision_token)


class JobStorage:
    SCHEMA_VERSION = 1
    MANIFEST = "jobs.manifest.json"
    DIRECTORY = "jobs"
    LEASE = "worker.lock"
    QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = "queued", "running", "completed", "failed", "cancelled"
    STATUSES = frozenset((QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED))
    TRANSITIONS = {QUEUED: (QUEUED, RUNNING, CANCELLED), RUNNING: (RUNNING, COMPLETED, FAILED, CANCELLED),
                   FAILED: (FAILED, RUNNING, CANCELLED), COMPLETED: (COMPLETED,), CANCELLED: (CANCELLED,)}
    KIND = "rag_job"
    PREPARED = "prepared.json"
    BATCHES = "batches"
    BATCH_FIELDS = frozenset(("key", "signature", "value"))
    CREATION_FIELDS = frozenset(("id", "created_at", "input_digest"))
    REQUIRED = CREATION_FIELDS | {"status", "phase", "cancel_requested", "resume_abandonment", "failures", "progress", "ingestion"}
    ATTEMPT_FIELDS = frozenset(("started_at", "pid", "configuration"))
    ERROR_FIELDS = frozenset(("failed_phase", "error", "diagnostic_code", "diagnostic"))
    FAILURE_FIELDS = ERROR_FIELDS | {"status", "started_at", "ended_at"}
    FIELDS = REQUIRED | ATTEMPT_FIELDS | ERROR_FIELDS | {"ended_at", "document_id", "document_ids", "resume_abandonment"}

    @staticmethod
    def _time(value):
        if not isinstance(value, str) or datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("RAG Job time requires timezone-aware ISO text")

    @staticmethod
    def _diagnostic(value):
        if (not isinstance(value["failed_phase"], str) or not value["failed_phase"]
                or not isinstance(value["diagnostic"], dict)
                or value["diagnostic"].keys() != {field.name for field in fields(Diagnostic)}):
            raise ValueError("Invalid RAG Job failure evidence")
        source = value["diagnostic"]["source"]
        if not isinstance(source, dict) or source.keys() != {field.name for field in fields(ResourceRef)}:
            raise ValueError("RAG Job diagnostic requires its complete source")
        diagnostic = Diagnostic.from_dict(value["diagnostic"])
        if (diagnostic.code != value["error"] or diagnostic.code != value["diagnostic_code"]
                or diagnostic.source is None):
            raise ValueError("RAG Job diagnostic disagrees with its failure")

    @classmethod
    def _state(cls, job, identifier):
        status, present = job["status"], job.keys()
        if (present - cls.FIELDS or type(job["cancel_requested"]) is not bool
                or not isinstance(job["failures"], list) or not isinstance(job["progress"], dict)
                or not isinstance(job["ingestion"], dict)):
            raise ValueError("Invalid RAG Job state fields")
        try:
            OperationProgress(ResourceRef(cls.KIND, identifier), job["phase"], **job["progress"])
        except TypeError as error:
            raise ValueError("Invalid RAG Job progress") from error
        attempted = bool(present & cls.ATTEMPT_FIELDS)
        if attempted:
            if not cls.ATTEMPT_FIELDS <= present:
                raise ValueError("RAG Job attempt evidence is incomplete")
            cls._time(job["started_at"])
            if (type(job["pid"]) is not int or job["pid"] < 1
                    or not isinstance(job["configuration"], str)
                    or re.fullmatch(r"[a-f0-9]{64}", job["configuration"]) is None):
                raise ValueError("Invalid RAG Job worker or configuration evidence")
        elif status in (cls.RUNNING, cls.COMPLETED, cls.FAILED):
            raise ValueError("RAG Job requires its stored attempt evidence")
        terminal_failure = status == cls.FAILED or (status == cls.CANCELLED and attempted)
        if terminal_failure:
            if not cls.ERROR_FIELDS <= present or "ended_at" not in job:
                raise ValueError("RAG Job requires its stored failure evidence")
            cls._diagnostic(job)
        elif present & cls.ERROR_FIELDS:
            raise ValueError("RAG Job has failure evidence outside a failed attempt")
        if status == cls.COMPLETED or terminal_failure:
            if "ended_at" not in job:
                raise ValueError("RAG Job requires its stored completion time")
            cls._time(job["ended_at"])
        elif "ended_at" in job:
            raise ValueError("RAG Job ended before a terminal attempt")
        if status == cls.RUNNING:
            if job["phase"] in cls.STATUSES:
                raise ValueError("Running RAG Job requires an active phase")
        elif job["phase"] != status:
            raise ValueError("RAG Job status and phase disagree")
        if not attempted and (job["failures"] or job["progress"] or job["ingestion"]):
            raise ValueError("Unstarted RAG Job has attempt observations")
        if status == cls.QUEUED and (attempted or job["cancel_requested"]):
            raise ValueError("Queued RAG Job has execution or cancellation evidence")
        if status == cls.CANCELLED and not attempted and not job["cancel_requested"]:
            raise ValueError("Unstarted cancelled RAG Job requires its cancellation")
        if status == cls.COMPLETED and (job["cancel_requested"] or
                job["progress"] != {"completed": 1, "total": 1, "unit": "job"}):
            raise ValueError("Completed RAG Job requires its publication progress")
        for failure in job["failures"]:
            if (not isinstance(failure, dict) or failure.keys() != cls.FAILURE_FIELDS
                    or failure["status"] != cls.FAILED):
                raise ValueError("Invalid RAG Job failure history")
            cls._time(failure["started_at"])
            cls._time(failure["ended_at"])
            cls._diagnostic(failure)

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
            raise ValueError("Cannot adopt existing RAG Job storage")
        make_directory(self.directory)
        atomic_json(self.manifest, {"schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id, "component": self.component, "jobs": {}})

    def _json(self, path):
        path = self.checked(path)
        if not path.is_file():
            raise ValueError("RAG Job evidence must be a regular file")
        return read_json(path)

    @classmethod
    def validate(cls, job, identifier):
        if (not isinstance(job, dict) or not cls.REQUIRED <= job.keys()
                or job["id"] != identifier or not isinstance(job["status"], str)
                or job["status"] not in cls.STATUSES
                or not isinstance(job["phase"], str) or not job["phase"]
                or not isinstance(job["input_digest"], str)
                or re.fullmatch(r"[a-f0-9]{64}", job["input_digest"]) is None):
            raise ValueError("Invalid RAG Job record")
        cls._time(job["created_at"])
        cls._state(job, identifier)
        if ("document_id" in job) == ("document_ids" in job):
            raise ValueError("RAG Job requires exactly one document declaration")
        if "document_id" in job:
            validate_name(job["document_id"])
        else:
            identifiers = job["document_ids"]
            if not isinstance(identifiers, list) or not identifiers:
                raise ValueError("RAG Job document IDs must be a nonempty list")
            for value in identifiers:
                validate_name(value)
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("RAG Job document IDs must be unique")
        if job["resume_abandonment"] is not None:
            if (not isinstance(job["resume_abandonment"], dict)
                    or job["resume_abandonment"].keys() != {field.name for field in fields(ResumeAbandonment)}):
                raise ValueError("RAG Job abandonment requires its complete receipt")
            ResumeAbandonment.from_dict(job["resume_abandonment"])
        return job

    def _lease_path(self, identifier):
        path = self.checked(self.path(identifier) / self.LEASE)
        if not path.is_file():
            raise ValueError("RAG Job worker lease is missing or not a regular file")
        expected = identifier.encode("utf-8")
        with path.open("rb") as stream:
            if stream.read(len(expected) + 1) != expected:
                raise ValueError("RAG Job worker lease identity changed")
        return path

    def inventory(self):
        manifest = self._json(self.manifest)
        if (not isinstance(manifest, dict)
                or manifest.keys() != {"schema_version", "project_id", "component", "jobs"}
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != self.SCHEMA_VERSION
                or manifest["project_id"] != self.project_id
                or manifest["component"] != self.component or not isinstance(manifest["jobs"], dict)):
            raise ValueError("Invalid RAG Job manifest")
        if not self.directory.is_dir():
            raise ValueError("RAG Job directory is missing")
        actual = set()
        for path in self.directory.iterdir():
            self.checked(path)
            if path.name == ".deletions":
                continue  # Existing explicit deletion intents have their own recovery contract.
            if not path.is_dir():
                raise ValueError("RAG Jobs must be directories")
            actual.add(validate_name(path.name))
        if actual != manifest["jobs"].keys():
            raise ValueError("RAG Jobs are missing or undeclared")
        records = {}
        for identifier, entry in manifest["jobs"].items():
            if (not isinstance(entry, dict) or entry.keys() != {"revision", "checkpoints"}
                    or not isinstance(entry["checkpoints"], dict)):
                raise ValueError("Invalid RAG Job inventory entry")
            for key, revision in entry["checkpoints"].items():
                self.checkpoint_path(identifier, key)
                if not isinstance(revision, str) or re.fullmatch(r"[a-f0-9]{64}", revision) is None:
                    raise ValueError("Invalid RAG Job checkpoint revision")
            root = self.path(identifier)
            job = self.validate(self._json(root / "job.json"), identifier)
            if entry["checkpoints"] and not self.ATTEMPT_FIELDS <= job.keys():
                raise ValueError("RAG Job checkpoints require their stored attempt evidence")
            if entry["revision"] != revision_token(job):
                raise ValueError("RAG Job revision changed")
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

    def validate_input(self, job, payload):
        if revision_token(payload) != job["input_digest"]:
            raise ValueError("RAG Job input changed")
        if "document_ids" in job:
            if (not isinstance(payload, dict) or payload.keys() != {"documents"}
                    or not isinstance(payload["documents"], list)):
                raise ValueError("Invalid RAG Job batch input")
            inputs, expected = payload["documents"], job["document_ids"]
        else:
            inputs, expected = [payload], [job["document_id"]]
        if len(inputs) != len(expected):
            raise ValueError("RAG Job input count changed")
        for source, identifier in zip(inputs, expected):
            self.validate_input_document(source)
            if source["identifier"] != identifier:
                raise ValueError("RAG Job input identity changed")
        return payload

    def read_input(self, identifier, job):
        path = self.checked(self.path(identifier) / "input.json")
        if not path.exists():
            raise FileNotFoundError("Required RAG Job input is missing")
        return self.validate_input(job, self._json(path))

    def checkpoint_path(self, identifier, key):
        if key != self.PREPARED:
            prefix = self.BATCHES + "/"
            if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(".json"):
                raise ValueError("Invalid RAG Job checkpoint path")
            validate_name(key[len(prefix):-len(".json")])
        return self.checked(self.path(identifier) / key)

    def validate_checkpoint(self, identifier, job, key, value):
        if not isinstance(value, dict):
            raise ValueError("RAG Job checkpoint must be an object")
        if key != self.PREPARED and (value.keys() != self.BATCH_FIELDS or value["key"] != key
                or not isinstance(value["signature"], str) or not value["signature"]
                or value["value"] is None):
            raise ValueError("Invalid RAG Job batch receipt")
        document_batch = re.fullmatch(r"batches/document_(0|[1-9][0-9]*)\.json", key)
        payload = self.read_input(identifier, job)
        if key != self.PREPARED and document_batch is None:
            self.validate_batch_checkpoint(job, key, value, payload)
            return
        if not self.ATTEMPT_FIELDS <= job.keys():
            raise ValueError("Prepared RAG document requires its Job attempt")
        if "document_ids" in job:
            inputs, expected = payload["documents"], job["document_ids"]
            if document_batch is not None:
                position = int(document_batch[1])
                if position >= len(inputs):
                    raise ValueError("Undeclared prepared RAG document position")
                inputs, expected = [inputs[position]], [expected[position]]
                documents = [value["value"]]
                if value["signature"] != revision_token({"input": inputs[0], "configuration": job["configuration"]}):
                    raise ValueError("Prepared RAG document batch signature changed")
            else:
                if value.keys() != {"documents"} or not isinstance(value["documents"], list):
                    raise ValueError("Invalid prepared RAG batch envelope")
                documents = value["documents"]
        else:
            if document_batch is not None:
                raise ValueError("Single-document RAG Job has a document batch")
            inputs, expected, documents = [payload], [job["document_id"]], [value]
        if len(documents) != len(expected):
            raise ValueError("Prepared RAG document count changed")
        for source, expected_id, document in zip(inputs, expected, documents):
            self.validate_document(document)
            if (document["id"] != expected_id
                    or document["revision"] != 1
                    or any(document[field] != source[field] for field in ("title", "content"))):
                raise ValueError("Prepared RAG document disagrees with its original Job input or attempt")
            if revision_token(document["metadata"]) != revision_token(source["metadata"]):
                raise ValueError("Prepared RAG document metadata differs from its original JSON input")
            if document.get("description") != source.get("description"):
                raise ValueError("Prepared RAG document description differs from its input")
            if document["preparation_configuration"] != job["configuration"]:
                raise ValueError("Prepared RAG document configuration differs from its Job attempt")

    def checkpoints(self, identifier):
        manifest, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError("RAG Job does not exist")
        declared = manifest["jobs"][identifier]["checkpoints"]
        root = self.path(identifier)
        actual = set()
        for path in root.iterdir():
            self.checked(path)
            if path.name in ("job.json", "input.json", self.LEASE):
                continue
            if path.name == self.BATCHES and path.is_dir():
                for entry in path.iterdir():
                    key = self.BATCHES + "/" + entry.name
                    self.checkpoint_path(identifier, key)
                    actual.add(key)
            elif path.name == self.PREPARED:
                actual.add(self.PREPARED)
            else:
                raise ValueError("Undeclared RAG Job contents")
        if actual - declared.keys():
            raise ValueError("Undeclared RAG Job checkpoint")
        if declared.keys() - actual:
            raise FileNotFoundError("Required RAG Job checkpoint is missing")
        values = {}
        for key, revision in declared.items():
            value = self._json(self.checkpoint_path(identifier, key))
            self.validate_checkpoint(identifier, records[identifier], key, value)
            if revision_token(value) != revision:
                raise ValueError("RAG Job checkpoint revision changed")
            values[key] = value
        return manifest, values

    def read_checkpoint(self, identifier, key):
        self.checkpoint_path(identifier, key)
        return self.checkpoints(identifier)[1].get(key)

    def write_checkpoint(self, identifier, key, value):
        manifest, _ = self.checkpoints(identifier)
        path = self.checkpoint_path(identifier, key)
        job = self.validate(self._json(self.path(identifier) / "job.json"), identifier)
        if not self.ATTEMPT_FIELDS <= job.keys():
            raise ValueError("RAG Job checkpoints require their stored attempt evidence")
        self.validate_checkpoint(identifier, job, key, value)
        atomic_json(path, value)
        manifest["jobs"][identifier]["checkpoints"][key] = revision_token(value)
        atomic_json(self.manifest, manifest)

    def clear_checkpoints(self, identifier):
        _, records = self.inventory()
        if records[identifier]["status"] != self.COMPLETED:
            raise ValueError("Only completed Job preparation copies can be cleared")
        manifest, values = self.checkpoints(identifier)
        from llm.services.infrastructure.storage import unlink_file
        if self.PREPARED in values:
            unlink_file(self.checkpoint_path(identifier, self.PREPARED))
        batches = self.checked(self.path(identifier) / self.BATCHES)
        if batches.exists():
            remove_named_tree(batches.parent, batches, self.BATCHES)
        manifest["jobs"][identifier]["checkpoints"] = {}
        atomic_json(self.manifest, manifest)

    def write_batch(self, identifier, key, signature, value):
        self.write_checkpoint(identifier, key, {"key": key, "signature": signature, "value": value})

    def create(self, job, payload):
        manifest, records = self.inventory()
        identifier = validate_name(job["id"])
        self.validate(job, identifier)
        if identifier in records:
            raise FileExistsError("RAG Job already exists")
        self.validate_input(job, payload)
        root = self.path(identifier)
        atomic_json(root / "input.json", payload)
        atomic_bytes(root / self.LEASE, identifier.encode("utf-8"))
        manifest["jobs"][identifier] = {"revision": revision_token(job), "checkpoints": {}}
        self._write(job, manifest)

    def _write(self, job, manifest):
        identifier = job["id"]
        atomic_json(self.path(identifier) / "job.json", job)
        manifest["jobs"][identifier]["revision"] = revision_token(job)
        atomic_json(self.manifest, manifest)

    def save(self, job):
        manifest, records = self.inventory()
        identifier = validate_name(job["id"])
        self.validate(job, identifier)
        if identifier not in records:
            raise FileNotFoundError("RAG Job does not exist")
        previous = records[identifier]
        immutable = self.CREATION_FIELDS
        immutable |= {key for key in ("document_id", "document_ids") if key in previous}
        if any(job.get(key) != previous[key] for key in immutable):
            raise ValueError("RAG Job creation evidence cannot change")
        if job["status"] not in self.TRANSITIONS[previous["status"]]:
            raise ValueError("Invalid RAG Job state transition")
        if previous["cancel_requested"] and not job["cancel_requested"]:
            raise ValueError("RAG Job cancellation cannot be revoked")
        if previous["resume_abandonment"] is not None and job["resume_abandonment"] != previous["resume_abandonment"]:
            raise ValueError("RAG Job abandonment evidence cannot change")
        self._write(job, manifest)

    def delete(self, identifier):
        manifest, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError("RAG Job does not exist")
        remove_named_tree(self.directory, self.path(identifier), identifier)
        del manifest["jobs"][identifier]
        atomic_json(self.manifest, manifest)

    def lease(self, identifier, *, probe=False):
        _, records = self.inventory()
        if identifier not in records:
            raise FileNotFoundError("RAG Job does not exist")
        path = self._lease_path(identifier)
        stream = os.fdopen(os.open(path, os.O_RDWR | os.O_NOFOLLOW), "r+b")
        try:
            opened, current = os.fstat(stream.fileno()), self.checked(path).stat()
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                    or stream.read(len(identifier.encode("utf-8")) + 1) != identifier.encode("utf-8")):
                raise ValueError("RAG Job worker lease changed during acquisition")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            if probe:
                return None
            raise ValueError("Indexing worker is still alive") from None
        except BaseException:
            stream.close()
            raise
        return stream
