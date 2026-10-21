"""RAG-owned Job inventory and persistent worker lease identity."""

from datetime import datetime
import fcntl
import os
import re
import stat

from llm.components.base import validate_name
from llm.core.checkpoints import ResumeAbandonment
from llm.services.infrastructure.storage import (
    atomic_bytes, atomic_json, make_directory, read_json, remove_named_tree, revision_token)


class JobStorage:
    SCHEMA_VERSION = 1
    MANIFEST = "jobs.manifest.json"
    DIRECTORY = "jobs"
    LEASE = "worker.lock"
    COMPLETED = "completed"
    STATUSES = frozenset(("queued", "running", COMPLETED, "failed", "cancelled"))
    PREPARED = "prepared.json"
    BATCHES = "batches"
    REQUIRED = frozenset(("id", "status", "created_at", "input_digest", "phase"))

    def __init__(self, project, component):
        self.project_id, self.component = project.id, component.name
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
        if not isinstance(job["created_at"], str):
            raise ValueError("RAG Job creation time is required")
        if datetime.fromisoformat(job["created_at"]).utcoffset() is None:
            raise ValueError("RAG Job creation time requires a timezone")
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
        if "resume_abandonment" in job:
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
            if entry["revision"] != revision_token(job):
                raise ValueError("RAG Job revision changed")
            self._lease_path(identifier)
            records[identifier] = job
        return manifest, records

    def validate_sources(self):
        """Backup/clone require input proof; resume diagnostics inspect missing inputs."""
        manifest, records = self.inventory()
        for identifier, job in records.items():
            if revision_token(self._json(self.path(identifier) / "input.json")) != job["input_digest"]:
                raise ValueError("RAG Job input changed")
            self.checkpoints(identifier)
        return manifest, records

    def checkpoint_path(self, identifier, key):
        if key != self.PREPARED:
            prefix = self.BATCHES + "/"
            if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(".json"):
                raise ValueError("Invalid RAG Job checkpoint path")
            validate_name(key[len(prefix):-len(".json")])
        return self.checked(self.path(identifier) / key)

    @classmethod
    def validate_checkpoint(cls, key, value):
        if not isinstance(value, dict):
            raise ValueError("RAG Job checkpoint must be an object")
        if key != cls.PREPARED and (value.keys() != {"signature", "value"}
                or not isinstance(value["signature"], str) or not value["signature"]
                or value["value"] is None):
            raise ValueError("Invalid RAG Job batch receipt")

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
            self.validate_checkpoint(key, value)
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
        self.validate_checkpoint(key, value)
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

    def create(self, job, payload):
        manifest, records = self.inventory()
        identifier = validate_name(job["id"])
        self.validate(job, identifier)
        if identifier in records:
            raise FileExistsError("RAG Job already exists")
        if revision_token(payload) != job["input_digest"]:
            raise ValueError("RAG Job input changed")
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
        immutable = self.REQUIRED - {"status", "phase"}
        immutable |= {key for key in ("document_id", "document_ids") if key in previous}
        if any(job.get(key) != previous[key] for key in immutable):
            raise ValueError("RAG Job creation evidence cannot change")
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
