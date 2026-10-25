"""Preset persistence and application through the existing settings service."""

from llm.services.schema import ProjectConfiguration

import asyncio
from .settings_contracts import SettingsOperation, SettingsBatch, PresetApplication

from ...config.presets import PresetStore
from ...config.preset_records import PresetDefinition, PresetIndex, PresetProject


class PresetService:
    VALIDATION_TITLE = "Preset"

    def __init__(self, settings):
        self.settings = settings
        config = settings.runtime.config
        self.store = PresetStore(config.launch_workspace)

    async def list(self):
        record = await asyncio.to_thread(self.store.read)
        return PresetIndex(record).selection().to_dict()

    async def export(self, identifier):
        record = await self.settings.load_project(identifier)
        form = ProjectConfiguration.view(record).values
        project = PresetProject.export(form)
        handle = await self.settings.project(identifier)
        from .agents import preset_agents
        project = project.with_agents(await preset_agents(handle))
        globals_ = await asyncio.to_thread(
            self.store.globals, self.settings.runtime.config.user_profile
        )
        return PresetDefinition.create(self.store.VERSION, globals_, project)

    async def save(self, name, value):
        PresetStore.validate(value)
        self.settings.validate_values(
            PresetDefinition(value).project.validation_values(self.VALIDATION_TITLE)
        )
        self._validate_agents(PresetDefinition(value).project.agents)
        await asyncio.to_thread(self.store.save, name, value)
        return await self.list()

    async def apply(self, name, identifier):
        stored = await asyncio.to_thread(self.store.read)
        value = PresetDefinition(
            PresetStore.validate(PresetIndex(stored).definition(name))
        )
        project = await self.settings.project(identifier)
        await self.settings.idle_sessions(project)
        record = await self.settings.load_project(identifier)
        view = ProjectConfiguration.view(record)
        candidate = value.project.application_values(view.values)
        self._validate_agents(value.project.agents)
        self._check_fixed(record, candidate)
        requests = [
            (
                SettingsOperation.SAVE_PROJECT.value,
                (identifier, candidate, view.config_version, view.values.components),
            )
        ]
        requests.extend(
            (SettingsOperation.SAVE_GLOBAL.value, (section, values))
            for section, values in value.globals.items()
        )
        batch = await self.settings.save_all(requests)
        if not SettingsBatch.error_from_record(batch):
            from .agents import apply_agents
            try:
                await apply_agents(project, value.project.agents)
                await asyncio.to_thread(self.store.select, name)
            except Exception as error:
                from ...errors.adapters import from_exception
                from ...errors.contracts import ErrorOrigin
                batch = SettingsBatch(
                    SettingsBatch.from_record(batch).results,
                    from_exception(error, origin=ErrorOrigin.BACKEND,
                                   operation=SettingsOperation.PRESET_APPLY),
                ).to_dict()
        return PresetApplication(
            batch,
            await self.settings.catalog(),
            await self.settings.load_project(identifier),
            await self.list(),
        ).to_dict()

    @staticmethod
    def _check_fixed(record, candidate):
        ProjectConfiguration.validate_edit(record, candidate)

    def _validate_agents(self, definitions):
        for definition in definitions.values():
            self.settings.runtime.backend.engines.resolve_agent(definition)
