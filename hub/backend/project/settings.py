"""Settings operations on the backend loop, through public LLM APIs only."""

from llm.services.schema import ProjectConfigForm, ProjectConfiguration

from copy import deepcopy
from dataclasses import asdict, replace

from ...config.preferences import PreferencesStore
from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from ...errors.adapters import from_exception
from ...errors.contracts import ErrorOrigin


class SettingsService:
    def __init__(self, runtime):
        self.runtime = runtime
        anchor = runtime.config.workspace if runtime.config._workspace_anchor is None else runtime.config._workspace_anchor
        self.preferences = PreferencesStore(anchor)

    def _schema(self):
        schema = self.runtime.backend.describe_project_config()
        schema["properties"]["conversation_storage"]["description"] = self.runtime.t("settings_storage_description")
        from ..interactions.approval import RISK_SCHEME
        schema["x-hub-risk-scheme"] = RISK_SCHEME
        from .settings_descriptions import describe_options
        describe_options(schema, self.runtime.t)
        return schema

    def _validate(self, values):
        from jsonschema import Draft202012Validator
        from llm.core.models import ProjectConfig
        from llm.core.json_data import JsonObject
        JsonObject.validate(values)
        Draft202012Validator(self._schema()).validate(values)
        form = ProjectConfigForm.from_dict(values)
        if not form.title.strip():
            raise ValueError(self.runtime.t("settings_title_required"))
        from ..execution.engine_selection import selected_engines
        if not selected_engines(ProjectConfig.from_dict(form.config), self.runtime.backend.engines.names()):
            raise ValueError(self.runtime.t("settings_engine_required"))
        return form

    async def _project(self, identifier):
        return await self.runtime.backend.projects.aload(identifier)

    async def _idle_sessions(self, project):
        sessions = await project.sessions.alist()
        for session in sessions:
            status = await session.run.astatus()
            if status.active_runs or status.queued_count or session.id in self.runtime.namer.tasks:
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
                from .usage import daily_usage
                usage.append({"id": data.id, "title": data.title,
                              "daily": daily_usage(totals.get("calls", [])),
                              **{key: totals[key] for key in ("call_count", "known_tokens", "reserved_tokens",
                                                            "unknown_calls", "period_seconds")}})
            except Exception as error:
                usage.append({"id": data.id, "title": data.title, "error": from_exception(error,
                    origin=ErrorOrigin.BACKEND, operation="model_usage")})
        preferences = {"profile": asdict(self.runtime.config.user_profile),
                       "general": asdict(GeneralSettings()), **self.preferences.load()}
        if preferences["general"]["workspace_root"] == "":
            anchor = (self.runtime.config.workspace if self.runtime.config._workspace_anchor is None
                      else self.runtime.config._workspace_anchor)
            preferences["general"]["workspace_root"] = str(anchor)
        return {"projects": projects, "schema": self._schema(),
                "host": self.runtime.backend.describe_host(),
                "usage": usage,
                "active_project": self.runtime.project.id,
                "preferences": preferences}

    async def list_projects(self):
        """Sidebar reads do not calculate usage or materialize configuration schemas."""
        rows = []
        for project in await self.runtime.backend.projects.alist():
            data = await project.aget_data()
            rows.append({"id": data.id, "title": data.title})
        return rows

    async def load_project(self, identifier):
        project = await self._project(identifier)
        result = await project.adescribe_config()
        # Keep the full registered catalog available for activation controls.
        return ProjectConfiguration.with_schema(result, self._schema())

    async def save_project(self, identifier, values, version, previous_components=None):
        form = self._validate(values)
        project = await self._project(identifier)
        current = await project.aget_data()
        previous = list(current.components) if previous_components is None else previous_components
        if list(current.components) != previous:
            raise ValueError(self.runtime.t("settings_components_changed"))
        candidate = deepcopy(form.config)
        await project.asave(title=form.title, config=candidate,
                            conversation_storage=form.conversation_storage, expected_version=version,
                            components=form.components, expected_components=previous)
        return await self.load_project(identifier)

    async def rename_project(self, identifier, title):
        if not title.strip():
            raise ValueError(self.runtime.t("settings_title_required"))
        project = await self._project(identifier)
        await project.asave(title=title.strip())
        return await self.load_project(identifier)

    async def create_project(self, values):
        from .defaults import creation_values, create_project
        values = creation_values(self.runtime.backend, values)
        form = self._validate(values)
        project = await create_project(self.runtime.backend, form.title, config=form.config,
            components=form.components, conversation_storage=form.conversation_storage,
            agent_id=self.runtime.config.agent_id, agent_definition=self.runtime.config.agent_definition)
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
        project = await self._project(identifier)
        sessions = await self._idle_sessions(project)
        if identifier == self.runtime.project.id:
            others = [item for item in await self.runtime.backend.projects.alist() if item.id != identifier]
            if not others:
                raise ValueError(self.runtime.t("project_delete_last"))
            await self.runtime.activate_project(others[0])
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
        import asyncio
        if section == "general":
            from ...config.workspace import validate_destination
            await asyncio.to_thread(validate_destination, values.get("workspace_root", ""))
        value = await asyncio.to_thread(self.preferences.save, section, values)
        if section == "profile":
            self.runtime.config = replace(self.runtime.config, user_profile=UserProfile(**value))
        elif section == "general":
            self.runtime.general = GeneralSettings(**value)
        return value

    async def save_all(self, requests):
        # Validate every draft before the first write. Public Project APIs keep
        # their own concurrency checks; report any partial success explicitly.
        from ...config.theme import HubTheme
        for operation, args in requests:
            if operation == "save_global":
                section, values = args
                {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}[section](**values)
                if section == "general":
                    import asyncio
                    from ...config.workspace import validate_destination
                    await asyncio.to_thread(validate_destination, values.get("workspace_root", ""))
            elif operation in ("save_project", "create_project"):
                values = deepcopy(args[1] if operation == "save_project" else args[0])
                if operation == "create_project":
                    from .defaults import creation_values
                    values = creation_values(self.runtime.backend, values)
                form = self._validate(values)
            else:
                raise ValueError("Unsupported settings save operation")
        results = []
        for operation, args in requests:
            try:
                results.append(await getattr(self, operation)(*args))
            except Exception as error:
                return {"results": results, "error": from_exception(error,
                    origin=ErrorOrigin.BACKEND, operation=operation, validation=True)}
        return {"results": results, "error": None}

    async def execute(self, operation, *args):
        if operation in ("preset_list", "preset_export", "preset_save", "preset_apply"):
            from .presets import PresetService
            return await getattr(PresetService(self), operation.removeprefix("preset_"))(*args)
        allowed = {"catalog", "list_projects", "load_project", "save_project", "create_project", "select_components",
                   "clone_project", "delete_project", "rename_project", "activate_project", "save_global", "save_all"}
        if operation not in allowed:
            raise ValueError("Unknown settings operation")
        return await getattr(self, operation)(*args)
