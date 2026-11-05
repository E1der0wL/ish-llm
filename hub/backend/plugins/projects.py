"""Dedicated Project lifecycle. No current-home selection and no private files."""

import asyncio
from copy import deepcopy

from llm.core.models import ProjectConfig, ConversationStorage
from .definitions import PluginDefinition, PluginIdentity
from .protocol import PluginProject, PluginRegistration, PluginSession
from ..session_contracts import InternalSession


class PluginProjects:
    MISSING_PROJECT = 'Plugin must have exactly one registered Project; register it first'
    CHANGED_OWNER = 'Plugin Project ownership changed'
    DUPLICATE_PROJECTS = 'Duplicate plugin Projects require explicit user resolution'
    RESERVED_OWNER = 'Plugin ownership is assigned by Hub'
    FOREIGN_SESSION = 'Session does not belong to this plugin'
    DUPLICATE_SESSIONS = 'Duplicate plugin Sessions require explicit user resolution'
    def __init__(self, backend):
        self.backend = backend
        self.lock = asyncio.Lock()
        self._projects = {}

    async def catalog(self):
        rows = []
        for project in await self.backend.projects.alist():
            record = await project.aget_data()
            owner = PluginIdentity.owner(record)
            if owner:
                rows.append(PluginProject(owner, project.id, record.title,
                                          tuple(await project.agents.alist())).to_dict())
        return rows

    async def project(self, plugin_id):
        PluginIdentity.validate(plugin_id)
        if plugin_id not in self._projects:
            matches = []
            for project in await self.backend.projects.alist():
                if PluginIdentity.owner(await project.aget_data()) == plugin_id:
                    matches.append(project)
            if len(matches) != 1:
                raise ValueError(PluginProjects.MISSING_PROJECT)
            self._projects[plugin_id] = matches[0].id
        project = await self.backend.projects.aload(self._projects[plugin_id])
        if PluginIdentity.owner(await project.aget_data()) != plugin_id:
            raise ValueError(PluginProjects.CHANGED_OWNER)
        return project

    async def register(self, plugin_id: str, definition: PluginDefinition):
        PluginIdentity.validate(plugin_id)
        async with self.lock:
            matches = [item for row in await self.catalog()
                       if (item := PluginProject.from_dict(row)).plugin_id == plugin_id]
            if len(matches) > 1:
                raise ValueError(PluginProjects.DUPLICATE_PROJECTS)
            created = not matches
            if created:
                config = ProjectConfig.from_dict(deepcopy(definition.config))
                if PluginIdentity.PROJECT_KEY in config.data:
                    raise ValueError(PluginProjects.RESERVED_OWNER)
                config.data[PluginIdentity.PROJECT_KEY] = plugin_id
                # No standard Hub creation template: no title jobs, planner,
                # postprocessing or forced final_review for this dedicated Project.
                project = await self.backend.projects.acreate(
                    definition.name, config=config.to_dict(), components=definition.components,
                    conversation_storage=ConversationStorage.FILE)
            else:
                project = await self.backend.projects.aload(matches[0].project_id)
            try:
                existing = set(await project.agents.alist())
                for name, value in definition.agents.items():
                    if name not in existing:
                        await project.agents.acreate(value, identifier=name)
            except BaseException:
                if created:
                    await asyncio.shield(project.adelete())
                raise
            self._projects[plugin_id] = project.id
            return PluginRegistration(plugin_id, project.id,
                                      tuple(await project.agents.alist()), created).to_dict()

    async def session(self, plugin_id, session_id):
        project = await self.project(plugin_id)
        session = await project.sessions.aload(session_id)
        record = await session.aget_data()
        if record.metadata.get(PluginIdentity.PROJECT_KEY) != plugin_id or not InternalSession.is_internal(record.metadata):
            raise ValueError(PluginProjects.FOREIGN_SESSION)
        return session

    async def open_session(self, plugin_id, key, agent_ids):
        PluginIdentity.validate(key)
        async with self.lock:
            project = await self.project(plugin_id)
            matches = []
            for session in await project.sessions.alist():
                record = await session.aget_data()
                if record.metadata.get(PluginIdentity.SESSION_KEY) == key:
                    matches.append(session)
            if len(matches) > 1:
                raise ValueError(PluginProjects.DUPLICATE_SESSIONS)
            if matches:
                session = await self.session(plugin_id, matches[0].id)
                # Session membership is user-owned after creation.
            else:
                session = await project.sessions.acreate(key, agent_ids=list(agent_ids))
                await session.asave(metadata={InternalSession.KEY: PluginIdentity.INTERNAL,
                    PluginIdentity.PROJECT_KEY: plugin_id, PluginIdentity.SESSION_KEY: key})
            return PluginSession(project.id, session.id).to_dict()
