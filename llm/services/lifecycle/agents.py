"""Independent Agent storage, serialized by the owning Project workspace.

Lifecycle access, registered Engine selection and reference checks belong to
the Agent manager. Repository methods never execute an Engine or activate a
Component. Every mutation validates the complete existing inventory first.
"""

import hashlib
import json

from llm.core.agents import AgentCatalog, AgentDefinition, AgentRecord
from llm.core.models import new_id
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import (
    atomic_bytes, atomic_json, child, make_directory, read_json, reject_links, unlink_file,
)


class AgentRepository:
    DIRECTORY = "agents"
    RECORDS = "records"
    MANIFEST = "records.manifest.json"

    def __init__(self, ownership):
        self.ownership = ownership

    def root(self, project):
        expected = child(self.ownership.path.parent, project.id)
        if project.paths.root.absolute() != expected.absolute():
            raise ValueError("Agent Project path mismatch")
        return reject_links(project.paths.root / self.DIRECTORY)

    @workspace_locked
    def initialize_new(self, project):
        """Only fresh Project creation can establish an empty Agent catalogue."""
        root = self.root(project)
        if project.paths.root.exists():
            raise FileExistsError("Agent initialization requires a new Project tree")
        make_directory(root / self.RECORDS)
        atomic_json(root / self.MANIFEST, AgentCatalog.empty(project.id))

    def _inventory(self, project):
        root = self.root(project)
        # No missing-root or missing-manifest fallback, including empty stores.
        manifest = read_json(reject_links(root / self.MANIFEST))
        AgentCatalog.validate(manifest, project_id=project.id)
        if {reject_links(path).name for path in root.iterdir()} != {self.MANIFEST, self.RECORDS}:
            raise ValueError("Agent storage contains undeclared entries")
        directory = reject_links(root / self.RECORDS)
        paths = {}
        for path in directory.iterdir():
            reject_links(path)
            if not path.is_file() or path.suffix != ".json":
                raise ValueError("Invalid Agent record file")
            paths[AgentDefinition.identifier(path.stem)] = path
        if paths.keys() != manifest["records"].keys():
            raise ValueError("Agent records are missing or undeclared")
        records = {}
        for identifier, path in sorted(paths.items()):
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest["records"][identifier]:
                raise ValueError("Agent record integrity mismatch")
            records[identifier] = AgentRecord.read(json.loads(raw), project_id=project.id, identifier=identifier)
        return manifest, records

    @workspace_locked
    def list(self, project):
        return {identifier: record["definition"] for identifier, record in self._inventory(project)[1].items()}

    @workspace_locked
    def snapshot(self, project, identifier):
        AgentDefinition.identifier(identifier)
        records = self._inventory(project)[1]
        if identifier not in records:
            raise FileNotFoundError(f"Unknown Agent: {identifier}")
        value = records[identifier]
        return {"agent_id": identifier, "definition": value["definition"], "revision": value["revision"]}

    @workspace_locked
    def load(self, project, identifier):
        return self.snapshot(project, identifier)["definition"]

    def _write(self, project, identifier, definition, manifest):
        value = AgentRecord.create(project.id, identifier, definition)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        root = self.root(project)
        manifest["records"][identifier] = hashlib.sha256(raw).hexdigest()
        atomic_bytes(root / self.RECORDS / (identifier + ".json"), raw)
        atomic_json(root / self.MANIFEST, manifest)

    @workspace_locked
    def create(self, project, definition, *, identifier=None):
        identifier = new_id() if identifier is None else AgentDefinition.identifier(identifier)
        AgentDefinition.validate(identifier, definition)
        manifest, records = self._inventory(project)
        if identifier in records:
            raise FileExistsError(f"Agent already exists: {identifier}")
        self._write(project, identifier, definition, manifest)
        return identifier

    @staticmethod
    def _check_revision(record, expected_revision):
        if not isinstance(expected_revision, str) or expected_revision != record["revision"]:
            raise ValueError("edit_conflict: Agent changed; reload before editing")

    @workspace_locked
    def save(self, project, identifier, definition, *, expected_revision):
        AgentDefinition.validate(identifier, definition)
        manifest, records = self._inventory(project)
        if identifier not in records:
            raise FileNotFoundError(f"Unknown Agent: {identifier}")
        self._check_revision(records[identifier], expected_revision)
        self._write(project, identifier, definition, manifest)

    @workspace_locked
    def delete(self, project, identifier, *, expected_revision):
        AgentDefinition.identifier(identifier)
        manifest, records = self._inventory(project)
        if identifier not in records:
            raise FileNotFoundError(f"Unknown Agent: {identifier}")
        self._check_revision(records[identifier], expected_revision)
        del manifest["records"][identifier]
        root = self.root(project)
        unlink_file(root / self.RECORDS / (identifier + ".json"))
        atomic_json(root / self.MANIFEST, manifest)

    @workspace_locked
    def clone(self, source, destination):
        records = self._inventory(source)[1]
        manifest, existing = self._inventory(destination)
        if existing:
            raise ValueError("Agent clone requires an empty destination catalogue")
        for identifier, record in records.items():
            self._write(destination, identifier, record["definition"], manifest)

    @workspace_locked
    def validate_backup(self, project):
        self._inventory(project)
