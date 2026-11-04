"""Dedicated Project lifecycle. No current-home selection and no private files."""

import asyncio
from copy import deepcopy

from llm.core.models import ProjectConfig, ConversationStorage
from .definitions import PluginDefinition, PluginIdentity
from ..session_contracts import InternalSession
from ..project.management_values import ResourceTimestamps


class PluginProjects:
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
                rows.append({"plugin_id": owner, "project_id": project.id, "name": record.title,
                             "agents": await project.agents.alist()})
        return rows

    async def project(self, plugin_id):
        PluginIdentity.validate(plugin_id)
        if plugin_id not in self._projects:
            matches = []
            for project in await self.backend.projects.alist():
                if PluginIdentity.owner(await project.aget_data()) == plugin_id:
                    matches.append(project)
            if len(matches) != 1:
                raise ValueError("Plugin must have exactly one registered Project; register it first")
            self._projects[plugin_id] = matches[0].id
        project = await self.backend.projects.aload(self._projects[plugin_id])
        if PluginIdentity.owner(await project.aget_data()) != plugin_id:
            raise ValueError("Plugin Project ownership changed")
        return project

    async def register(self, plugin_id: str, definition: PluginDefinition):
        PluginIdentity.validate(plugin_id)
        async with self.lock:
            matches = [row for row in await self.catalog() if row["plugin_id"] == plugin_id]
            if len(matches) > 1:
                raise ValueError("Duplicate plugin Projects require explicit user resolution")
            created = not matches
            if created:
                config = ProjectConfig.from_dict(deepcopy(definition.config))
                if PluginIdentity.PROJECT_KEY in config.data:
                    raise ValueError("Plugin ownership is assigned by Hub")
                config.data[PluginIdentity.PROJECT_KEY] = plugin_id
                # No standard Hub creation template: no title jobs, planner,
                # postprocessing or forced final_review for this dedicated Project.
                project = await self.backend.projects.acreate(
                    definition.name, config=config.to_dict(), components=definition.components,
                    conversation_storage=ConversationStorage.FILE)
            else:
                project = await self.backend.projects.aload(matches[0]["project_id"])
            try:
                existing = set(await project.agents.alist())
                for name, value in definition.agents.items():
                    if name not in existing:
                        await project.agents.acreate(ResourceTimestamps.created(value), identifier=name)
            except BaseException:
                if created:
                    await asyncio.shield(project.adelete())
                raise
            self._projects[plugin_id] = project.id
            return {"plugin_id": plugin_id, "project_id": project.id,
                    "agents": await project.agents.alist(), "created": created}

    async def session(self, plugin_id, session_id):
        project = await self.project(plugin_id)
        session = await project.sessions.aload(session_id)
        record = await session.aget_data()
        if record.metadata.get(PluginIdentity.PROJECT_KEY) != plugin_id or not InternalSession.is_internal(record.metadata):
            raise ValueError("Session does not belong to this plugin")
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
                raise ValueError("Duplicate plugin Sessions require explicit user resolution")
            if matches:
                session = await self.session(plugin_id, matches[0].id)
                # Session membership is user-owned after creation.
            else:
                session = await project.sessions.acreate(key, agent_ids=list(agent_ids))
                await session.asave(metadata={InternalSession.KEY: PluginIdentity.INTERNAL,
                    PluginIdentity.PROJECT_KEY: plugin_id, PluginIdentity.SESSION_KEY: key})
            return {"project_id": project.id, "session_id": session.id}
