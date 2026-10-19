"""Session participant references to the owning Project's single Agent catalogue."""

from llm.core.agents import AgentDefinition
from llm.core.models import SESSION_STORAGE_VERSION, SessionStatus
from llm.services.infrastructure.storage import async_method


class SessionAgents:
    def __init__(self, session):
        self.session, self.app = session, session.app

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)

    def _current(self):
        self.app._check_open()
        manager = self.app.project_manager.sessions
        current = manager.load(self.session.project._snapshot, self.session.id)
        manager._owner(current)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        return current

    def list(self):
        with self.app.project_manager.ownership.scope():
            return list(self._current().agent_ids)

    def attach(self, identifier):
        AgentDefinition.identifier(identifier)
        with self.app.project_manager.ownership.scope():
            current = self._current()
            project = self.app.project_manager.sessions._owner(current)
            self.app.project_manager.agents.load(project, identifier)
            current.agent_ids = sorted(set((*current.agent_ids, identifier)))
            current.storage_version = SESSION_STORAGE_VERSION
            self.app.project_manager.sessions.repository.save(current)
            return list(current.agent_ids)

    def detach(self, identifier):
        AgentDefinition.identifier(identifier)
        with self.app.project_manager.ownership.scope():
            current = self._current()
            current.agent_ids = [value for value in current.agent_ids if value != identifier]
            current.storage_version = SESSION_STORAGE_VERSION
            self.app.project_manager.sessions.repository.save(current)
            return list(current.agent_ids)

    alist = async_method(list)
    aattach = async_method(attach)
    adetach = async_method(detach)
