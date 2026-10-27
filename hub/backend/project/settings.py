"""Settings operations on the backend loop, through public LLM APIs only."""

from hub.locales.keys import MessageKey

from llm.services.schema import ProjectConfigForm, ProjectConfiguration

from copy import deepcopy
from dataclasses import replace

from ...config.preferences import PreferencesStore
from ...config.profile import UserProfile
from ...config.general import GeneralSettings, GeneralSettingsDraft
from ...config.preference_records import PreferenceSnapshot
from ...config.sections import PreferenceSection
from llm.core.schema import SchemaKeyword
from .settings_contracts import (
    SettingsOperation,
    SettingsDispatch,
    ProjectSummary,
    ProjectUsageRecord,
    ProjectUsageFailure,
    SettingsCatalog,
    SettingsBatch,
)
from ...errors.adapters import from_exception
from ...errors.contracts import ErrorOrigin
from ..progress import OperationProgress


class SettingsService:
    UNSPECIFIED_COMPONENTS = None
    DELETED = True
    VALIDATION_ERROR = True

    def __init__(self, runtime):
        self.runtime = runtime
        self.preferences = PreferencesStore(runtime.config.launch_workspace)

    def _schema(self):
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_SCHEMA)
        schema = self.runtime.backend.describe_project_config()
        schema[SchemaKeyword.PROPERTIES][
            ProjectConfigForm.conversation_storage.__name__
        ][SchemaKeyword.DESCRIPTION] = self.runtime.t(
            MessageKey.SETTINGS_STORAGE_DESCRIPTION
        )
        from ..interactions.approval import RISK_SCHEME

        schema[SettingsCatalog.RISK_SCHEME_FIELD] = RISK_SCHEME
        from .settings_descriptions import describe_options

        describe_options(schema, self.runtime.t)
        return schema

    def validate_values(self, values):
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_VALIDATE)
        from jsonschema import Draft202012Validator
        from llm.core.models import ProjectConfig
        from llm.core.json_data import JsonObject

        JsonObject.validate(values)
        Draft202012Validator(self._schema()).validate(values)
        form = ProjectConfigForm.from_dict(values)
        if not form.title.strip():
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_TITLE_REQUIRED))
        from ..execution.engine_selection import selected_engines

        if not selected_engines(
            ProjectConfig.from_dict(form.config), self.runtime.backend.engines.names()
        ):
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_ENGINE_REQUIRED))
        return form

    async def project(self, identifier):
        return await self.runtime.backend.projects.aload(identifier)

    async def idle_sessions(self, project):
        sessions = await project.sessions.alist()
        for session in sessions:
            status = await session.run.astatus()
            if (
                status.active_runs
                or status.queued_count
                or self.runtime.namer.is_active(session.id)
            ):
                raise ValueError(self.runtime.t(MessageKey.SETTINGS_PROJECT_BUSY))
        return sessions

    async def catalog(self):
        projects = []
        usage = []
        available = await self.runtime.backend.projects.alist()
        for index, project in enumerate(available):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_USAGE, index, len(available))
            data = await project.aget_data()
            projects.append(ProjectSummary.from_data(data).to_dict())
            try:
                totals = await project.amodel_usage()
                usage.append(ProjectUsageRecord.collect(data, totals).to_dict())
            except Exception as error:
                usage.append(
                    ProjectUsageFailure(
                        data.id,
                        data.title,
                        from_exception(
                            error,
                            origin=ErrorOrigin.BACKEND,
                            operation=ProjectUsageFailure.OPERATION,
                        ),
                    ).to_dict()
                )
        preferences = PreferenceSnapshot.catalog(
            self.runtime.config.user_profile,
            self.preferences.load,
            lambda: self.runtime.config.launch_workspace,
        )

        return SettingsCatalog(
            projects,
            self._schema(),
            self.runtime.backend.describe_host(),
            usage,
            self.runtime.project.id,
            preferences,
        ).to_dict()

    async def list_projects(self):
        """Sidebar reads do not calculate usage or materialize configuration schemas."""
        rows = []
        for project in await self.runtime.backend.projects.alist():
            data = await project.aget_data()
            rows.append(ProjectSummary.from_data(data).to_dict())
        return rows

    async def load_project(self, identifier):
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_READ)
        project = await self.project(identifier)
        result = await project.adescribe_config()
        # Keep the full registered catalog available for activation controls.
        return ProjectConfiguration.with_schema(result, self._schema())

    async def save_project(
        self, identifier, values, version, previous_components=UNSPECIFIED_COMPONENTS
    ):
        from .memory_setup import MemorySetup

        original_form = self.validate_values(values)
        config, selected = MemorySetup.prepare(original_form.config, original_form.components)
        prepared = deepcopy(values)
        prepared[ProjectConfigForm.config.__name__] = config.to_dict()
        prepared[ProjectConfigForm.components.__name__] = selected
        form = self.validate_values(prepared)
        project = await self.project(identifier)
        current = await project.aget_data()
        previous = (
            list(current.components)
            if previous_components is self.UNSPECIFIED_COMPONENTS
            else previous_components
        )
        if list(current.components) != previous:
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_COMPONENTS_CHANGED))
        candidate = deepcopy(form.config)
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_WRITE)
        await project.asave(
            title=form.title,
            config=candidate,
            conversation_storage=form.conversation_storage,
            expected_version=version,
            components=form.components,
            expected_components=previous,
        )
        await MemorySetup.ensure_sources(project)
        return await self.load_project(identifier)

    async def rename_project(self, identifier, title):
        if not title.strip():
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_TITLE_REQUIRED))
        project = await self.project(identifier)
        await project.asave(title=title.strip())
        return await self.load_project(identifier)

    async def create_project(self, values):
        from .defaults import creation_values, create_project

        values = creation_values(self.runtime.backend, values)
        form = self.validate_values(values)
        project = await create_project(
            self.runtime.backend,
            form.title,
            config=form.config,
            components=form.components,
            conversation_storage=form.conversation_storage,
            agent_id=self.runtime.config.agent_id,
            agent_definition=self.runtime.config.agent_definition,
        )
        return await self.load_project(project.id)

    async def select_components(self, identifier, names, previous):
        project = await self.project(identifier)
        if list((await project.aget_data()).components) != previous:
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_COMPONENTS_CHANGED))
        await project.components.aselect(names)
        return list((await project.aget_data()).components)

    async def clone_project(self, identifier, title):
        if not title.strip():
            raise ValueError(self.runtime.t(MessageKey.SETTINGS_TITLE_REQUIRED))
        project = await self.project(identifier)
        sessions = await self.idle_sessions(project)
        try:
            for session in sessions:
                await session.run.shutdown()
            clone = await project.aclone(title=title)
            return await self.load_project(clone.id)
        finally:
            for session in sessions:
                if self.runtime.session_is_open(session.id):
                    await session.run.start()

    async def delete_project(self, identifier):
        project = await self.project(identifier)
        sessions = await self.idle_sessions(project)
        if identifier == self.runtime.project.id:
            others = [
                item
                for item in await self.runtime.backend.projects.alist()
                if item.id != identifier
            ]
            if not others:
                raise ValueError(self.runtime.t(MessageKey.PROJECT_DELETE_LAST))
            await self.runtime.activate_project(others[0])
        try:
            for session in sessions:
                await session.run.shutdown()
            await project.adelete()
        except BaseException:
            for session in sessions:
                if self.runtime.session_is_open(session.id):
                    await session.run.start()
            raise
        self.runtime.forget_open_sessions(session.id for session in sessions)
        return self.DELETED

    async def activate_project(self, identifier):
        await self.runtime.activate_project(await self.project(identifier))
        return await self.runtime.snapshot()

    async def save_global(self, section, values):
        import asyncio

        if section == PreferenceSection.GENERAL:
            from ...config.workspace import validate_destination

            await asyncio.to_thread(
                validate_destination, GeneralSettingsDraft(values).workspace_selection
            )
        value = await asyncio.to_thread(self.preferences.save, section, values)
        if section == PreferenceSection.PROFILE:
            self.runtime.config = replace(
                self.runtime.config, user_profile=UserProfile(**value)
            )
        elif section == PreferenceSection.GENERAL:
            self.runtime.general = GeneralSettings(**value)
        return value

    async def save_all(self, requests):
        # Validate every draft before the first write. Public Project APIs keep
        # their own concurrency checks; report any partial success explicitly.
        for index, (operation, args) in enumerate(requests):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_VALIDATE, index, len(requests))
            if operation == SettingsOperation.SAVE_GLOBAL:
                section, values = args
                PreferencesStore.input_record(section, values)
                if section == PreferenceSection.GENERAL:
                    import asyncio
                    from ...config.workspace import validate_destination

                    await asyncio.to_thread(
                        validate_destination,
                        GeneralSettingsDraft(values).workspace_selection,
                    )
            elif operation in SettingsDispatch.PROJECT_WRITES:
                values = SettingsDispatch.project_values(operation, args)
                if operation == SettingsOperation.CREATE_PROJECT:
                    from .defaults import creation_values

                    values = creation_values(self.runtime.backend, values)
                form = self.validate_values(values)
            else:
                raise ValueError(SettingsDispatch.UNSUPPORTED_SAVE)
        results = []
        for index, (operation, args) in enumerate(requests):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_WRITE, index, len(requests))
            try:
                results.append(await getattr(self, operation)(*args))
            except Exception as error:
                return SettingsBatch(
                    results,
                    from_exception(
                        error,
                        origin=ErrorOrigin.BACKEND,
                        operation=operation,
                        validation=self.VALIDATION_ERROR,
                    ),
                ).to_dict()
        return SettingsBatch(results).to_dict()

    async def execute(self, operation, *args):
        OperationProgress.report(OperationProgress.settings(operation))
        if operation in SettingsDispatch.PRESETS:
            from .presets import PresetService

            return await getattr(
                PresetService(self), SettingsDispatch.preset_method(operation)
            )(*args)
        if operation not in SettingsDispatch.DIRECT:
            raise ValueError(SettingsDispatch.UNKNOWN_OPERATION)
        return await getattr(self, operation)(*args)
