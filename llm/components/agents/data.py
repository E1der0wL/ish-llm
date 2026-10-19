"""Legacy Agent Component handle; all definition operations use ProjectAgents."""

from llm.services.lifecycle.components import ComponentData
from llm.services.lifecycle.agents import ProjectAgents
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method


class AgentData(ComponentData):
    def _domain(self):
        project, _ = self._current()
        handle = ProjectAgents(self.access, self.registry.agent_repository, project,
            components=self.registry, sessions=self._sessions, steps=self._steps)
        if self._async_runner is not None:
            handle.bind_runtime(runner=self._async_runner, access_check=self._access_check)
        return handle

    def _validate_input(self, identifier, definition):
        _, component = self._current()
        component.validate_record(identifier, definition)

    @workspace_locked
    def create(self, data, *, identifier=None):
        self._validate_input(identifier or "pending", data)
        return self._domain().create(data, identifier=identifier)

    @workspace_locked
    def load(self, identifier):
        return self._domain().load(identifier)

    @workspace_locked
    def list(self):
        return self._domain().list()

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        self._validate_input(identifier, data)
        return self._domain().save(identifier, data, expected_version=expected_version)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        self._validate_input(identifier, {**self._domain().load(identifier), **changes})
        return self._domain().update(identifier, changes, expected_version=expected_version)

    @workspace_locked
    def delete(self, identifier, *, expected_version=None):
        return self._domain().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def snapshot(self, identifier):
        return self._domain().snapshot(identifier)

    @workspace_locked
    def revise(self, identifier, definition, *, expected_revision):
        self._validate_input(identifier, definition)
        return self._domain().revise(identifier, definition, expected_revision=expected_revision)

    @workspace_locked
    def prompt(self, identifier):
        return self._domain().prompt(identifier)

    @workspace_locked
    def update_prompt(self, identifier, system_prompt, *, expected_revision):
        self._validate_input(identifier, {**self._domain().load(identifier), "system_prompt": system_prompt})
        return self._domain().update_prompt(identifier, system_prompt, expected_revision=expected_revision)

    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)
    asave = async_method(save)
    aupdate = async_method(update)
    adelete = async_method(delete)
    asnapshot = async_method(snapshot)
    arevise = async_method(revise)
    aprompt = async_method(prompt)
    aupdate_prompt = async_method(update_prompt)
