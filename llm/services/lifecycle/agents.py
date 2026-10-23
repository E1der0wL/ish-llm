"""Independent Agent storage, serialized by the owning Project workspace.

Lifecycle access, registered Engine selection and reference checks belong to
the Agent manager. Repository methods never execute an Engine or activate a
Component. Every mutation validates the complete existing inventory first.
"""

from llm.core.checkpoint_records import CheckpointRecords

import hashlib
import json
import asyncio
from copy import deepcopy

from llm.core.agents import AgentCatalog, AgentDefinition, AgentRecord
from llm.core.models import new_id
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import (
    StorageIO, async_method, atomic_bytes, atomic_json, child, make_directory, read_json, reject_links, unlink_file,
)


class AgentRepository:
    DIRECTORY = ProjectPaths.AGENTS
    RECORDS = "records"
    MANIFEST = "records.manifest.json"
    RECORD_SUFFIX = ".json"

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
        atomic_json(root / self.MANIFEST, AgentCatalog.empty(project.id).to_dict())

    def _inventory(self, project):
        return self._inventory_at(self.root(project), project.id)

    def _inventory_at(self, root, project_id):
        # No missing-root or missing-manifest fallback, including empty stores.
        manifest = AgentCatalog.read(read_json(reject_links(root / self.MANIFEST)), project_id=project_id)
        if {reject_links(path).name for path in root.iterdir()} != {self.MANIFEST, self.RECORDS}:
            raise ValueError("Agent storage contains undeclared entries")
        directory = reject_links(root / self.RECORDS)
        paths = {}
        for path in directory.iterdir():
            reject_links(path)
            if not path.is_file() or path.suffix != self.RECORD_SUFFIX:
                raise ValueError("Invalid Agent record file")
            paths[AgentDefinition.identifier(path.stem)] = path
        if paths.keys() != manifest.records.keys():
            raise ValueError("Agent records are missing or undeclared")
        records = {}
        for identifier, path in sorted(paths.items()):
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest.records[identifier]:
                raise ValueError("Agent record integrity mismatch")
            records[identifier] = AgentRecord.read(json.loads(raw), project_id=project_id, identifier=identifier)
        return manifest, records

    @workspace_locked
    def list(self, project):
        return {identifier: record.definition for identifier, record in self._inventory(project)[1].items()}

    @workspace_locked
    def snapshot(self, project, identifier):
        AgentDefinition.identifier(identifier)
        records = self._inventory(project)[1]
        if identifier not in records:
            raise FileNotFoundError(f"Unknown Agent: {identifier}")
        value = records[identifier]
        return {"agent_id": identifier, "definition": value.definition, "revision": value.revision}

    @workspace_locked
    def load(self, project, identifier):
        return self.snapshot(project, identifier)["definition"]

    def _write(self, project, identifier, definition, manifest):
        value = AgentRecord.create(project.id, identifier, definition)
        raw = json.dumps(value.to_dict(), ensure_ascii=False, allow_nan=False).encode("utf-8")
        root = self.root(project)
        manifest = manifest.with_record(identifier, hashlib.sha256(raw).hexdigest())
        atomic_bytes(root / self.RECORDS / (identifier + self.RECORD_SUFFIX), raw)
        atomic_json(root / self.MANIFEST, manifest.to_dict())
        return manifest

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
        if not isinstance(expected_revision, str) or expected_revision != record.revision:
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
        manifest = manifest.without_record(identifier)
        root = self.root(project)
        unlink_file(root / self.RECORDS / (identifier + self.RECORD_SUFFIX))
        atomic_json(root / self.MANIFEST, manifest.to_dict())

    @workspace_locked
    def clone(self, source, destination):
        records = self._inventory(source)[1]
        manifest, existing = self._inventory(destination)
        if existing:
            raise ValueError("Agent clone requires an empty destination catalogue")
        for identifier, record in records.items():
            manifest = self._write(destination, identifier, record.definition, manifest)

    @workspace_locked
    def validate_backup(self, project):
        self._inventory(project)

    def read_backup(self, project):
        """Explicit archive reads validate identical bytes without publishing or repairing them."""
        return self._inventory_at(reject_links(project.paths.agents), project.id)[1]

    @workspace_locked
    def references(self, project):
        references = []
        for identifier, definition in self.list(project).items():
            resources = definition.get("resources", {})
            references.extend({"component": "skills", "identifier": skill, "source": identifier}
                              for skill in resources.get("skills", []))
            if "prompt" in resources:
                references.append({"component": "prompts", "identifier": resources["prompt"], "source": identifier})
            references.extend({"component": "mcp", "identifier": server, "source": identifier}
                              for server in resources.get("mcp", {}))
            if resources.get("rag"):
                references.append({"component": "rag", "identifier": None, "source": identifier})
        return references


class AgentManager:
    """Authoritative Project access, selected Engine validation and Agent lifetime."""

    def __init__(self, access, repository, *, engines, components, sessions, steps):
        self.access, self.repository = access, repository
        self.engines, self.components = engines, components
        self.sessions, self.steps = sessions, steps
        self.ownership = access.repository.ownership

    def _validate(self, identifier, definition):
        AgentDefinition.validate(identifier, definition)
        self.engines.resolve_agent(definition)

    @workspace_locked
    def references(self, project):
        """Common resources and selected Engine resources retain their owners."""
        project = self.access.require(project)
        values = self.repository.references(project)
        for identifier, definition in self.repository.list(project).items():
            engine = self.engines.resolve_agent(definition)
            describe = getattr(engine, "agent_references", None)
            if describe is None:
                continue
            references = describe()
            if not isinstance(references, tuple):
                raise TypeError("Engine agent_references must return a tuple")
            for reference in references:
                if (not isinstance(reference, dict) or reference.keys() != {"component", "identifier"}
                        or any(not isinstance(value, str) or not value for value in reference.values())):
                    raise ValueError("Engine Agent references require Component and record identifiers")
                values.append({**reference, "source": identifier})
        return values

    @workspace_locked
    def describe(self, project):
        self.access.require(project)
        return {"schema": deepcopy(AgentDefinition.schema), "storage_version": AgentRecord.STORAGE_VERSION,
                "engines": list(self.engines.names())}

    @workspace_locked
    def create(self, project, definition, *, identifier=None):
        project = self.access.require(project)
        identifier = new_id() if identifier is None else AgentDefinition.identifier(identifier)
        self._validate(identifier, definition)
        return self.repository.create(project, definition, identifier=identifier)

    @workspace_locked
    def list(self, project):
        return self.repository.list(self.access.require(project))

    @workspace_locked
    def load(self, project, identifier):
        return self.repository.load(self.access.require(project), identifier)

    @workspace_locked
    def snapshot(self, project, identifier):
        return self.repository.snapshot(self.access.require(project), identifier)

    @workspace_locked
    def revise(self, project, identifier, definition, *, expected_revision):
        project = self.access.require(project)
        self._validate(identifier, definition)
        self.repository.save(project, identifier, definition, expected_revision=expected_revision)
        return {"agent_id": identifier, "revision": AgentDefinition.revision(definition),
                "applies_to": "subsequent_runs"}

    @workspace_locked
    def update(self, project, identifier, changes, *, expected_revision):
        from llm.core.models import ProjectConfig
        ProjectConfig.validate_json(changes)
        definition = {**self.load(project, identifier), **deepcopy(changes)}
        self.revise(project, identifier, definition, expected_revision=expected_revision)
        return definition

    @workspace_locked
    def prompt(self, project, identifier):
        snapshot = self.snapshot(project, identifier)
        definition = snapshot["definition"]
        return {"agent_id": identifier, "revision": snapshot["revision"],
                **({"system_prompt": definition["system_prompt"]} if "system_prompt" in definition else {})}

    @workspace_locked
    def update_prompt(self, project, identifier, system_prompt, *, expected_revision):
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise TypeError("Agent system_prompt must be text or null")
        definition = self.load(project, identifier)
        definition["system_prompt"] = system_prompt
        return self.revise(project, identifier, definition, expected_revision=expected_revision)

    def _require_unreferenced(self, project, identifier):
        from llm.engines.agents import AgentExecution
        from llm.services.runtime.checkpoints import CheckpointRepository
        self.components.require_unreferenced(project, "agents", identifier)
        for name in self.components.names():
            reader = getattr(self.components.get(name), "agent_references", None)
            if reader is not None:
                for reference in reader(project, self.engines):
                    if reference["identifier"] == identifier:
                        raise ValueError(f"Agent is referenced by {name}/{reference['source']}")
        checkpoints = CheckpointRepository()
        for session in self.sessions.list(project, include_deleted=True):
            from llm.services.runtime.requests import RequestAdmission
            from llm.core.steering import is_queued_request
            for message in self.sessions.conversations(session).list():
                if is_queued_request(message):
                    admission = RequestAdmission.read(message, project_id=project.id, session_id=session.id)
                    if identifier in admission["recipients"]:
                        raise ValueError("Agent is referenced by a queued request")
            for run in self.sessions.run_repository.list(session):
                if run.agent_id == identifier:
                    raise ValueError("Agent is referenced by a Run")
                for step in self.steps.list(run):
                    # AgentExecution owns this top-level Step execution binding.
                    if (step.metadata.get("agent_id") == identifier
                            or identifier in AgentExecution.referenced_agents(step.metadata)):
                        raise ValueError("Agent is referenced by a Run Step")
                for name in checkpoints.names(run):
                    checkpoint = checkpoints.load(run, name)
                    if any(identifier in AgentExecution.referenced_agents(record)
                           for record in CheckpointRecords.from_checkpoint(checkpoint).records.values()):
                        raise ValueError("Agent is referenced by a Checkpoint")
            if identifier in session.agent_ids:
                raise ValueError("Agent is referenced by Session participation")

    @workspace_locked
    def delete(self, project, identifier, *, expected_revision):
        project = self.access.require(project)
        self._require_unreferenced(project, identifier)
        self.repository.delete(project, identifier, expected_revision=expected_revision)


class AgentHandle:
    """Project-bound Agent API using the host's ordinary storage and lifetime lane."""

    def __init__(self, manager, project):
        self.manager, self.project = manager, deepcopy(project)
        self._async_storage = StorageIO(manager.ownership)
        self._async_runner = self._access_check = self._async_loop = None

    def bind_runtime(self, *, runner, access_check):
        if not callable(runner) or not callable(access_check):
            raise TypeError("Agent runtime hooks must be callable")
        self._async_runner, self._access_check = runner, access_check
        return self

    async def _async_call(self, operation, *args, **kwargs):
        if self._async_runner is not None:
            return await self._async_runner(operation, *args, **kwargs)
        loop = asyncio.get_running_loop()
        if self._async_loop is None:
            self._async_loop = loop
        elif self._async_loop is not loop:
            raise RuntimeError("Use Agent async methods on their original event loop")
        return await self._async_storage.run(operation, *args, **kwargs)

    def _call(self, name, *args, **kwargs):
        if self._access_check is not None:
            self._access_check()
        return getattr(self.manager, name)(self.project, *args, **kwargs)

    def describe(self):
        return self._call("describe")

    def create(self, definition, *, identifier=None):
        return self._call("create", definition, identifier=identifier)

    def load(self, identifier):
        return self._call("load", identifier)

    def list(self):
        return self._call("list")

    def snapshot(self, identifier):
        return self._call("snapshot", identifier)

    def revise(self, identifier, definition, *, expected_revision):
        return self._call("revise", identifier, definition, expected_revision=expected_revision)

    def save(self, identifier, definition, *, expected_revision):
        return self.revise(identifier, definition, expected_revision=expected_revision)

    def update(self, identifier, changes, *, expected_revision):
        return self._call("update", identifier, changes, expected_revision=expected_revision)

    def prompt(self, identifier):
        return self._call("prompt", identifier)

    def update_prompt(self, identifier, system_prompt, *, expected_revision):
        return self._call("update_prompt", identifier, system_prompt, expected_revision=expected_revision)

    def delete(self, identifier, *, expected_revision):
        return self._call("delete", identifier, expected_revision=expected_revision)

    adescribe = async_method(describe)
    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)
    asnapshot = async_method(snapshot)
    arevise = async_method(revise)
    asave = async_method(save)
    aupdate = async_method(update)
    aprompt = async_method(prompt)
    aupdate_prompt = async_method(update_prompt)
    adelete = async_method(delete)
