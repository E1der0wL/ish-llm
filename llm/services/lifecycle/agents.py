"""Independent Agent storage, serialized by the owning Project workspace.

Lifecycle access, registered Engine selection and reference checks belong to
the Agent manager. Repository methods never execute an Engine or activate a
Component. Every mutation validates the complete existing inventory first.
"""

from llm.core.resource_references import ResourceTarget, StoredResourceReference
from llm.core.resource_references import AgentUse, AgentReferenceReader

from llm.core.agent_views import AgentSnapshot, AgentDescription, AgentRevision, AgentPrompt

from llm.core.checkpoint_records import CheckpointRecords

import hashlib
import json
import asyncio
from copy import deepcopy
from dataclasses import dataclass

from llm.core.agents import AgentCatalog, AgentDefinition, AgentRecord
from llm.core.models import new_id
from llm.core.json_data import JsonObject
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import (
    StorageIO, async_method, atomic_bytes, atomic_json, child, make_directory, read_json, reject_links, unlink_file,
)


@dataclass(frozen=True, slots=True)
class AgentInventory:
    catalog: AgentCatalog
    records: dict[str, AgentRecord]


class AgentRepository:
    DIRECTORY = ProjectPaths.AGENTS
    RECORDS = "records"
    MANIFEST = "records.manifest.json"
    RECORD_SUFFIX = ".json"
    JSON_ENCODING = "utf-8"
    ENSURE_ASCII = False
    PATH_MISMATCH = "Agent Project path mismatch"
    NEW_PROJECT_REQUIRED = "Agent initialization requires a new Project tree"
    UNDECLARED_ENTRIES = "Agent storage contains undeclared entries"
    INVALID_RECORD_FILE = "Invalid Agent record file"
    INVENTORY_MISMATCH = "Agent records are missing or undeclared"
    INTEGRITY_MISMATCH = "Agent record integrity mismatch"
    UNKNOWN_AGENT = "Unknown Agent: {identifier}"
    EXISTING_AGENT = "Agent already exists: {identifier}"
    EDIT_CONFLICT = "edit_conflict: Agent changed; reload before editing"
    NONEMPTY_CLONE = "Agent clone requires an empty destination catalogue"

    def __init__(self, ownership):
        self.ownership = ownership

    def root(self, project):
        expected = child(self.ownership.path.parent, project.id)
        if project.paths.root.absolute() != expected.absolute():
            raise ValueError(self.PATH_MISMATCH)
        return reject_links(project.paths.root / self.DIRECTORY)

    @workspace_locked
    def initialize_new(self, project):
        """Only fresh Project creation can establish an empty Agent catalogue."""
        root = self.root(project)
        if project.paths.root.exists():
            raise FileExistsError(self.NEW_PROJECT_REQUIRED)
        make_directory(root / self.RECORDS)
        atomic_json(root / self.MANIFEST, AgentCatalog.empty(project.id).to_dict())

    def _inventory(self, project):
        return self._inventory_at(self.root(project), project.id)

    def _inventory_at(self, root, project_id):
        # No missing-root or missing-manifest fallback, including empty stores.
        manifest = AgentCatalog.read(read_json(reject_links(root / self.MANIFEST)), project_id=project_id)
        if {reject_links(path).name for path in root.iterdir()} != {self.MANIFEST, self.RECORDS}:
            raise ValueError(self.UNDECLARED_ENTRIES)
        directory = reject_links(root / self.RECORDS)
        paths = {}
        for path in directory.iterdir():
            reject_links(path)
            if not path.is_file() or path.suffix != self.RECORD_SUFFIX:
                raise ValueError(self.INVALID_RECORD_FILE)
            paths[AgentDefinition.identifier(path.stem)] = path
        if paths.keys() != manifest.records.keys():
            raise ValueError(self.INVENTORY_MISMATCH)
        records = {}
        for identifier, path in sorted(paths.items()):
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest.records[identifier]:
                raise ValueError(self.INTEGRITY_MISMATCH)
            records[identifier] = AgentRecord.read(json.loads(raw), project_id=project_id, identifier=identifier)
        return AgentInventory(manifest, records)

    @workspace_locked
    def list(self, project):
        return {identifier: record.definition for identifier, record in self._inventory(project).records.items()}

    @workspace_locked
    def snapshot(self, project, identifier):
        AgentDefinition.identifier(identifier)
        records = self._inventory(project).records
        if identifier not in records:
            raise FileNotFoundError(self.UNKNOWN_AGENT.format(identifier=identifier))
        value = records[identifier]
        return AgentSnapshot.from_record(value).to_dict()

    @workspace_locked
    def load(self, project, identifier):
        # Inventory has already validated the record and its revision. Avoid
        # serializing/revalidating an observation merely to read its definition.
        return self.load_many(project, (identifier,))[identifier]

    @workspace_locked
    def load_many(self, project, identifiers):
        identifiers = tuple(identifiers)
        for identifier in identifiers:
            AgentDefinition.identifier(identifier)
        if not identifiers:
            return {}
        records = self._inventory(project).records
        result = {}
        for identifier in identifiers:
            if identifier not in records:
                raise FileNotFoundError(self.UNKNOWN_AGENT.format(identifier=identifier))
            result[identifier] = records[identifier].definition
        return result

    def _write(self, project, identifier, definition, manifest):
        value = AgentRecord.create(project.id, identifier, definition)
        raw = json.dumps(value.to_dict(), ensure_ascii=self.ENSURE_ASCII, allow_nan=JsonObject.ALLOW_NON_FINITE).encode(self.JSON_ENCODING)
        root = self.root(project)
        manifest = manifest.with_record(identifier, hashlib.sha256(raw).hexdigest())
        atomic_bytes(root / self.RECORDS / (identifier + self.RECORD_SUFFIX), raw)
        atomic_json(root / self.MANIFEST, manifest.to_dict())
        return manifest

    @workspace_locked
    def create(self, project, definition, *, identifier=None):
        identifier = new_id() if identifier is None else AgentDefinition.identifier(identifier)
        AgentDefinition.validate(identifier, definition)
        inventory = self._inventory(project)
        manifest, records = inventory.catalog, inventory.records
        if identifier in records:
            raise FileExistsError(self.EXISTING_AGENT.format(identifier=identifier))
        self._write(project, identifier, definition, manifest)
        return identifier

    @staticmethod
    def _check_revision(record, expected_revision):
        if not isinstance(expected_revision, str) or expected_revision != record.revision:
            raise ValueError(AgentRepository.EDIT_CONFLICT)

    @workspace_locked
    def save(self, project, identifier, definition, *, expected_revision):
        AgentDefinition.validate(identifier, definition)
        inventory = self._inventory(project)
        manifest, records = inventory.catalog, inventory.records
        if identifier not in records:
            raise FileNotFoundError(self.UNKNOWN_AGENT.format(identifier=identifier))
        self._check_revision(records[identifier], expected_revision)
        self._write(project, identifier, definition, manifest)

    @workspace_locked
    def delete(self, project, identifier, *, expected_revision):
        AgentDefinition.identifier(identifier)
        inventory = self._inventory(project)
        manifest, records = inventory.catalog, inventory.records
        if identifier not in records:
            raise FileNotFoundError(self.UNKNOWN_AGENT.format(identifier=identifier))
        self._check_revision(records[identifier], expected_revision)
        manifest = manifest.without_record(identifier)
        root = self.root(project)
        unlink_file(root / self.RECORDS / (identifier + self.RECORD_SUFFIX))
        atomic_json(root / self.MANIFEST, manifest.to_dict())

    @workspace_locked
    def clone(self, source, destination):
        records = self._inventory(source).records
        inventory = self._inventory(destination)
        manifest, existing = inventory.catalog, inventory.records
        if existing:
            raise ValueError(self.NONEMPTY_CLONE)
        for identifier, record in records.items():
            manifest = self._write(destination, identifier, record.definition, manifest)

    @workspace_locked
    def validate_backup(self, project):
        self._inventory(project)

    def read_backup(self, project):
        """Explicit archive reads validate identical bytes without publishing or repairing them."""
        return self._inventory_at(reject_links(project.paths.agents), project.id).records

    @workspace_locked
    def references(self, project):
        from llm.components.skills import SkillComponent
        from llm.components.prompts import PromptComponent
        from llm.components.mcp import MCPComponent
        from llm.components.rag import RAGComponent

        references = []
        for identifier, definition in self.list(project).items():
            resources = definition.get(AgentDefinition.Field.RESOURCES, {})
            references.extend(StoredResourceReference.from_target(ResourceTarget(SkillComponent.name, skill), identifier).to_dict()
                              for skill in resources.get(AgentDefinition.ResourceField.SKILLS, []))
            if AgentDefinition.ResourceField.PROMPT in resources:
                references.append(StoredResourceReference.from_target(
                    ResourceTarget(PromptComponent.name, resources[AgentDefinition.ResourceField.PROMPT]), identifier).to_dict())
            references.extend(StoredResourceReference.from_target(ResourceTarget(MCPComponent.name, server), identifier).to_dict()
                              for server in resources.get(AgentDefinition.ResourceField.MCP, {}))
            if resources.get(AgentDefinition.ResourceField.RAG):
                references.append(StoredResourceReference.from_target(
                    ResourceTarget(RAGComponent.name, ResourceTarget.WHOLE_COMPONENT), identifier).to_dict())
        return references


class AgentManager:
    """Authoritative Project access, selected Engine validation and Agent lifetime."""

    INVALID_REFERENCES = "Engine agent_references must return a tuple"
    INVALID_PROMPT = "Agent system_prompt must be text or null"
    COMPONENT_REFERENCE = "Agent is referenced by {name}/{source}"
    QUEUED_REFERENCE = "Agent is referenced by a queued request"
    RUN_REFERENCE = "Agent is referenced by a Run"
    STEP_REFERENCE = "Agent is referenced by a Run Step"
    CHECKPOINT_REFERENCE = "Agent is referenced by a Checkpoint"
    SESSION_REFERENCE = "Agent is referenced by Session participation"
    INCLUDE_DELETED = True

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
            describe = AgentReferenceReader.from_owner(engine)
            if describe is None:
                continue
            references = describe()
            if not isinstance(references, tuple):
                raise TypeError(self.INVALID_REFERENCES)
            for reference in references:
                values.append(StoredResourceReference.from_target(
                    ResourceTarget.from_engine(reference), identifier).to_dict())
        return values

    @workspace_locked
    def describe(self, project, *, engine=None):
        self.access.require(project)
        schema = AgentDefinition.describe() if engine is None else self.engines.describe_agent(engine)
        return AgentDescription(schema, AgentRecord.STORAGE_VERSION, list(self.engines.names())).to_dict()

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
        return AgentRevision(identifier, AgentDefinition.revision(definition)).to_dict()

    @workspace_locked
    def update(self, project, identifier, changes, *, expected_revision):
        from llm.core.json_data import JsonObject
        JsonObject.validate(changes)
        definition = {**self.load(project, identifier), **deepcopy(changes)}
        self.revise(project, identifier, definition, expected_revision=expected_revision)
        return definition

    @workspace_locked
    def prompt(self, project, identifier):
        snapshot = AgentSnapshot.read(self.snapshot(project, identifier))
        return AgentPrompt.from_definition(identifier, snapshot.revision, snapshot.definition).to_dict()

    @workspace_locked
    def update_prompt(self, project, identifier, system_prompt, *, expected_revision):
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise TypeError(self.INVALID_PROMPT)
        definition = self.load(project, identifier)
        definition[AgentDefinition.Field.SYSTEM_PROMPT.value] = system_prompt
        return self.revise(project, identifier, definition, expected_revision=expected_revision)

    def _require_unreferenced(self, project, identifier):
        from llm.engines.agents import AgentExecution
        from llm.services.runtime.checkpoints import CheckpointRepository
        self.components.require_unreferenced(project, AgentDefinition.REFERENCE_COMPONENT, identifier)
        for name in self.components.names():
            reader = AgentReferenceReader.from_owner(self.components.get(name))
            if reader is not None:
                for value in reader(project, self.engines):
                    reference = AgentUse.from_dict(value)
                    if reference.identifier == identifier:
                        raise ValueError(self.COMPONENT_REFERENCE.format(name=name, source=reference.source))
        checkpoints = CheckpointRepository()
        for session in self.sessions.list(project, include_deleted=self.INCLUDE_DELETED):
            from llm.services.runtime.requests import RequestAdmission
            from llm.core.steering import is_queued_request
            for message in self.sessions.conversations(session).list():
                if is_queued_request(message):
                    admission = RequestAdmission.read(message, project_id=project.id, session_id=session.id)
                    if identifier in admission.recipients:
                        raise ValueError(self.QUEUED_REFERENCE)
            for run in self.sessions.run_repository.list(session):
                if run.agent_id == identifier:
                    raise ValueError(self.RUN_REFERENCE)
                for step in self.steps.list(run):
                    # AgentExecution owns this top-level Step execution binding.
                    if (AgentExecution.step_agent_id(step) == identifier
                            or identifier in AgentExecution.referenced_agents(step.metadata)):
                        raise ValueError(self.STEP_REFERENCE)
                for name in checkpoints.names(run):
                    checkpoint = checkpoints.load(run, name)
                    if any(identifier in AgentExecution.referenced_agents(record)
                           for record in CheckpointRecords.from_checkpoint(checkpoint).records.values()):
                        raise ValueError(self.CHECKPOINT_REFERENCE)
            if identifier in session.agent_ids:
                raise ValueError(self.SESSION_REFERENCE)

    @workspace_locked
    def delete(self, project, identifier, *, expected_revision):
        project = self.access.require(project)
        self._require_unreferenced(project, identifier)
        self.repository.delete(project, identifier, expected_revision=expected_revision)


class AgentHandle:
    """Project-bound Agent API using the host's ordinary storage and lifetime lane."""

    INVALID_HOOKS = "Agent runtime hooks must be callable"
    WRONG_LOOP = "Use Agent async methods on their original event loop"

    def __init__(self, manager, project):
        self.manager, self.project = manager, deepcopy(project)
        self._async_storage = StorageIO(manager.ownership)
        self._async_runner = self._access_check = self._async_loop = None

    def bind_runtime(self, *, runner, access_check):
        if not callable(runner) or not callable(access_check):
            raise TypeError(self.INVALID_HOOKS)
        self._async_runner, self._access_check = runner, access_check
        return self

    async def _async_call(self, operation, *args, **kwargs):
        if self._async_runner is not None:
            return await self._async_runner(operation, *args, **kwargs)
        loop = asyncio.get_running_loop()
        if self._async_loop is None:
            self._async_loop = loop
        elif self._async_loop is not loop:
            raise RuntimeError(self.WRONG_LOOP)
        return await self._async_storage.run(operation, *args, **kwargs)

    def _call(self, name, *args, **kwargs):
        if self._access_check is not None:
            self._access_check()
        return getattr(self.manager, name)(self.project, *args, **kwargs)

    def describe(self, *, engine=None):
        return self._call(AgentManager.describe.__name__, engine=engine)

    def create(self, definition, *, identifier=None):
        return self._call(AgentManager.create.__name__, definition, identifier=identifier)

    def load(self, identifier):
        return self._call(AgentManager.load.__name__, identifier)

    def list(self):
        return self._call(AgentManager.list.__name__)

    def snapshot(self, identifier):
        return self._call(AgentManager.snapshot.__name__, identifier)

    def revise(self, identifier, definition, *, expected_revision):
        return self._call(AgentManager.revise.__name__, identifier, definition, expected_revision=expected_revision)

    def save(self, identifier, definition, *, expected_revision):
        return self.revise(identifier, definition, expected_revision=expected_revision)

    def update(self, identifier, changes, *, expected_revision):
        return self._call(AgentManager.update.__name__, identifier, changes, expected_revision=expected_revision)

    def prompt(self, identifier):
        return self._call(AgentManager.prompt.__name__, identifier)

    def update_prompt(self, identifier, system_prompt, *, expected_revision):
        return self._call(AgentManager.update_prompt.__name__, identifier, system_prompt, expected_revision=expected_revision)

    def delete(self, identifier, *, expected_revision):
        return self._call(AgentManager.delete.__name__, identifier, expected_revision=expected_revision)

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
