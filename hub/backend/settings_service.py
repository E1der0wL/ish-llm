"""Settings operations on the backend loop, through public LLM APIs only."""

from copy import deepcopy
from dataclasses import asdict, replace

from ..config.preferences import PreferencesStore
from ..config.profile import UserProfile
from ..config.general import GeneralSettings


class SettingsService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.preferences = PreferencesStore(runtime.config.workspace)

    def _schema(self):
        schema = self.runtime.backend.project_schema()
        config = schema["properties"]["config"]["properties"]
        # Provider arguments are open JSON in llm. Describe this common option
        # for Hub's form without modifying the backend schema or inventing a default.
        for engine in config["parameters"]["properties"]["engines"]["properties"].values():
            completion = engine.get("properties", {}).get("config", {}).get("properties", {}).get("completion", {}).get("properties")
            if completion is not None:
                completion.setdefault("reasoning_effort", {
                    "type": ["string", "null"], "minLength": 1,
                    "description": self.runtime.t("settings_reasoning_effort"),
                })
        schema["properties"]["conversation_storage"]["description"] = self.runtime.t("settings_storage_description")
        from .approval import RISK_SCHEME
        schema["x-hub-risk-scheme"] = RISK_SCHEME
        return schema

    def _validate(self, values):
        from jsonschema import Draft202012Validator
        from llm.core.models import ProjectConfig
        ProjectConfig.validate_settings(values)
        Draft202012Validator(self._schema()).validate(values)
        if not values.get("title", "").strip():
            raise ValueError(self.runtime.t("settings_title_required"))
        from .engine_selection import selected_engines
        if not selected_engines(ProjectConfig(values["config"]), self.runtime.backend.engines.names()):
            raise ValueError(self.runtime.t("settings_engine_required"))

    async def _project(self, identifier):
        return await self.runtime.backend.projects.aload(identifier)

    async def _idle_sessions(self, project):
        sessions = await project.sessions.alist()
        for session in sessions:
            status = await session.run.astatus()
            if status.active_run_id or status.queued_count or session.id in self.runtime.namer.tasks:
                raise ValueError(self.runtime.t("settings_project_busy"))
        return sessions

    async def catalog(self):
        projects = []
        usage = []
        for project in await self.runtime.backend.projects.alist():
            data = await project.aget_data()
            projects.append({"id": data.id, "title": data.title})
            try:
                totals = await project.amodel_usage()
                usage.append({"id": data.id, "title": data.title,
                              **{key: totals[key] for key in ("call_count", "known_tokens", "reserved_tokens",
                                                            "unknown_calls", "period_seconds")}})
            except Exception as error:
                usage.append({"id": data.id, "title": data.title, "error": str(error)})
        return {"projects": projects, "schema": self._schema(),
                "host": self.runtime.backend.host_configuration(),
                "usage": usage,
                "active_project": self.runtime.project.id,
                "preferences": {"profile": asdict(self.runtime.config.user_profile),
                                "general": asdict(GeneralSettings()), **self.preferences.load()}}

    async def load_project(self, identifier):
        project = await self._project(identifier)
        result = await project.aconfiguration()
        # Keep the full registered catalog available for activation controls.
        result["schema"] = self._schema()
        return result

    async def save_project(self, identifier, values, version, previous_components=None):
        self._validate(values)
        project = await self._project(identifier)
        current = await project.aget_data()
        previous = list(current.components) if previous_components is None else previous_components
        if list(current.components) != previous:
            raise ValueError(self.runtime.t("settings_components_changed"))
        candidate = deepcopy(values["config"])
        await project.asave(title=values["title"], config=candidate,
                            conversation_storage=values["conversation_storage"], expected_version=version,
                            components=values["components"], expected_components=previous)
        return await self.load_project(identifier)

    async def rename_project(self, identifier, title):
        if not title.strip():
            raise ValueError(self.runtime.t("settings_title_required"))
        project = await self._project(identifier)
        await project.asave(title=title.strip())
        return await self.load_project(identifier)

    async def create_project(self, values):
        values = deepcopy(values)
        values.setdefault("conversation_storage", "file")
        values["components"] = list(self._schema()["properties"]["components"]["items"]["enum"])
        self._validate(values)
        from .defaults import create_project
        project = await create_project(self.runtime.backend, values["title"], config=values["config"],
            components=values["components"], conversation_storage=values["conversation_storage"])
        return await self.load_project(project.id)

    async def select_components(self, identifier, names, previous):
        project = await self._project(identifier)
        if list((await project.aget_data()).components) != previous:
            raise ValueError(self.runtime.t("settings_components_changed"))
        await project.components.aselect(names)
        return list((await project.aget_data()).components)

    async def clone_project(self, identifier, title):
        if not title.strip():
            raise ValueError(self.runtime.t("settings_title_required"))
        project = await self._project(identifier)
        sessions = await self._idle_sessions(project)
        try:
            for session in sessions:
                await session.run.shutdown()
            clone = await project.aclone(title=title)
            return await self.load_project(clone.id)
        finally:
            for session in sessions:
                if session.id in self.runtime._opened_sessions:
                    await session.run.start()

    async def delete_project(self, identifier):
        if identifier == self.runtime.project.id:
            raise ValueError(self.runtime.t("settings_delete_active"))
        project = await self._project(identifier)
        sessions = await self._idle_sessions(project)
        try:
            for session in sessions:
                await session.run.shutdown()
            await project.adelete()
        except BaseException:
            for session in sessions:
                if session.id in self.runtime._opened_sessions:
                    await session.run.start()
            raise
        self.runtime._opened_sessions.difference_update(session.id for session in sessions)
        return True

    async def activate_project(self, identifier):
        await self.runtime.activate_project(await self._project(identifier))
        return await self.runtime.snapshot()

    async def save_global(self, section, values):
        value = self.preferences.save(section, values)
        if section == "profile":
            self.runtime.config = replace(self.runtime.config, user_profile=UserProfile(**value))
        elif section == "general":
            self.runtime.general = GeneralSettings(**value)
        return value

    async def execute(self, operation, *args):
        allowed = {"catalog", "load_project", "save_project", "create_project", "select_components",
                   "clone_project", "delete_project", "rename_project", "activate_project", "save_global"}
        if operation not in allowed:
            raise ValueError("Unknown settings operation")
        return await getattr(self, operation)(*args)
