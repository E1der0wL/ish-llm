"""Project Agent storage and lifecycle, independent of Component activation."""

from copy import deepcopy
import asyncio
import json

from llm.core.agents import AgentDefinition
from llm.core.models import new_id, ProjectConfig
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import (
    atomic_json, async_method, check_revision, make_directory, reject_links,
    StorageIO, unlink_file,
)


class AgentRepository:
    """One catalogue at the existing agents/records path; reads never migrate it."""

    storage_version = 1

    def root(self, project):
        # External Component reference hooks historically require only root.
        # Keep that protocol; the independent domain owns the agents suffix.
        root = project.paths.root / "agents"
        reject_links(root)
        if (root / "component.json").exists():
            raise ValueError("Legacy component.json requires explicit migration")
        return root

    def path(self, project, identifier):
        path = self.root(project) / "records" / (AgentDefinition.identifier(identifier) + ".json")
        reject_links(path)
        return path

    def initialize(self, project):
        make_directory(self.root(project) / "records")

    def load(self, project, identifier):
        value = json.loads(self.path(project, identifier).read_text(encoding="utf-8"))
        ProjectConfig.validate_json(value)
        if "storage_version" in value:
            if (type(value["storage_version"]) is not int or value["storage_version"] != self.storage_version
                    or set(value) != {"storage_version", "id", "definition", "revision"}
                    or value["id"] != identifier):
                raise ValueError("Unsupported or mismatched Agent record envelope")
            data = value["definition"]
            AgentDefinition.validate(identifier, data)
            if value["revision"] != AgentDefinition.revision(data):
                raise ValueError("Agent record revision mismatch")
        else:
            # Legacy AgentComponent JSON is an explicit read format, never
            # rewritten simply because a facade/Engine reads the definition.
            data = value
            AgentDefinition.validate(identifier, data)
        return deepcopy(data)

    def list(self, project):
        root = self.root(project) / "records"
        reject_links(root)
        return {path.stem: self.load(project, path.stem) for path in sorted(root.glob("*.json"))}

    def _write(self, project, identifier, data):
        AgentDefinition.validate(identifier, data)
        atomic_json(self.path(project, identifier), {
            "storage_version": self.storage_version, "id": identifier,
            "definition": deepcopy(data), "revision": AgentDefinition.revision(data),
        })

    def create(self, project, data, *, identifier=None):
        identifier = new_id() if identifier is None else identifier
        if self.path(project, identifier).exists():
            raise FileExistsError("Agent record already exists")
        self._write(project, identifier, data)
        return identifier

    def save(self, project, identifier, data):
        self.load(project, identifier)  # Reject corrupt/future records before overwriting.
        self._write(project, identifier, data)

    def update(self, project, identifier, changes):
        ProjectConfig.validate_json(changes)
        data = {**self.load(project, identifier), **deepcopy(changes)}
        self.save(project, identifier, data)
        return data

    def delete(self, project, identifier):
        self.load(project, identifier)
        unlink_file(self.path(project, identifier))

    def clone(self, source, destination):
        for identifier, data in self.list(source).items():
            self.create(destination, data, identifier=identifier)

    def references(self, project):
        result = []
        for identifier, data in self.list(project).items():
            resources = data.get("resources", {})
            selections = {"skills": resources.get("skills", []), "tools": data.get("tools", []),
                          "prompts": [resources["prompt"]] if resources.get("prompt") else [],
                          "mcp": list(resources.get("mcp", {}))}
            for component, names in selections.items():
                result.extend({"component": component, "identifier": name, "source": identifier}
                              for name in names)
        return result

    def resolve(self, project):
        return {"configuration": {}, "records": self.list(project)}


class ProjectAgents:
    """Lifecycle-checked Agent facade; revisions are definition-content hashes.

    Engine existence is checked for new public domain writes. Legacy offline
    Component editing can omit that environment; execution still validates the
    selected Engine in its own (possibly nested Graph) registry.
    """

    def __init__(self, access, repository, project, *, components, engines=None, sessions=None, steps=None):
        self.access, self.repository, self.project = access, repository, deepcopy(project)
        self.components, self.engines = components, engines
        self.sessions, self.steps = sessions, steps
        self.ownership = access.repository.ownership
        self._async_storage = StorageIO(self.ownership)
        self._async_runner = self._access_check = self._async_loop = None

    def bind_runtime(self, *, runner, access_check, **_):
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

    def _current(self):
        if self._access_check is not None:
            self._access_check()
        return self.access.require(self.project)

    def _validate(self, identifier, data):
        AgentDefinition.validate(identifier, data)
        if self.engines is not None and data["engine"] not in self.engines.names():
            raise ValueError("Unregistered Agent engine: " + data["engine"])

    @workspace_locked
    def describe(self):
        self._current()
        return {"schema": deepcopy(AgentDefinition.schema), "storage_version": self.repository.storage_version,
                "engines": list(self.engines.names()) if self.engines is not None else None}

    @workspace_locked
    def create(self, data, *, identifier=None):
        project = self._current()
        self._validate(identifier or "pending", data)
        identifier = self.repository.create(project, data, identifier=identifier)
        log_event(project.paths.logs, "agent.created", entity_id=identifier)
        return identifier

    @workspace_locked
    def load(self, identifier):
        return self.repository.load(self._current(), identifier)

    @workspace_locked
    def list(self):
        return self.repository.list(self._current())

    @workspace_locked
    def snapshot(self, identifier):
        data = self.repository.load(self._current(), identifier)
        return {"agent_id": identifier, "definition": data, "revision": AgentDefinition.revision(data)}

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        project = self._current()
        check_revision(self.repository.load(project, identifier), expected_version)
        self._validate(identifier, data)
        self.repository.save(project, identifier, data)
        log_event(project.paths.logs, "agent.updated", entity_id=identifier)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        project = self._current()
        ProjectConfig.validate_json(changes)
        data = self.repository.load(project, identifier)
        check_revision(data, expected_version)
        result = {**data, **deepcopy(changes)}
        self.save(identifier, result, expected_version=AgentDefinition.revision(data))
        return result

    @workspace_locked
    def revise(self, identifier, definition, *, expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError("Agent revision is required")
        project = self._current()
        if AgentDefinition.revision(self.repository.load(project, identifier)) != expected_revision:
            raise ValueError("Agent changed; reload its definition before updating")
        self.save(identifier, definition, expected_version=expected_revision)
        return {"agent_id": identifier, "revision": AgentDefinition.revision(definition),
                "applies_to": "subsequent_runs"}

    @workspace_locked
    def prompt(self, identifier):
        snapshot = self.snapshot(identifier)
        data = snapshot["definition"]
        return {"agent_id": identifier, "revision": snapshot["revision"],
                **({"system_prompt": data["system_prompt"]} if "system_prompt" in data else {})}

    @workspace_locked
    def update_prompt(self, identifier, system_prompt, *, expected_revision):
        if system_prompt is not None and not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be a string or None")
        data = self.load(identifier)
        data["system_prompt"] = system_prompt
        return self.revise(identifier, data, expected_revision=expected_revision)

    def _require_unreferenced(self, project, identifier):
        self.components.require_unreferenced(project, "agents", identifier)
        if self.sessions is None:
            return
        def references(value):
            if isinstance(value, dict):
                return value.get("agent_id") == identifier or any(references(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return any(references(v) for v in value)
            return False
        for session in self.sessions.list(project, include_deleted=True):
            if identifier in getattr(session, "agent_ids", ()):
                raise ValueError("Agent is attached to a Session")
            for run in self.sessions.run_repository.list(session):
                if getattr(run, "agent_id", None) == identifier or references(run.metadata):
                    raise ValueError("Agent is referenced by a Run")
                if self.steps is not None and any(references(step.metadata) for step in self.steps.list(run)):
                    raise ValueError("Agent is referenced by a nested Run Step")

    @workspace_locked
    def delete(self, identifier, *, expected_version=None):
        project = self._current()
        check_revision(self.repository.load(project, identifier), expected_version)
        self._require_unreferenced(project, identifier)
        self.repository.delete(project, identifier)
        log_event(project.paths.logs, "agent.deleted", entity_id=identifier)

    adescribe = async_method(describe)
    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)
    asnapshot = async_method(snapshot)
    asave = async_method(save)
    aupdate = async_method(update)
    arevise = async_method(revise)
    aprompt = async_method(prompt)
    aupdate_prompt = async_method(update_prompt)
    adelete = async_method(delete)
