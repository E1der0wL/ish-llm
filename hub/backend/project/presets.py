"""Preset persistence and application through the existing settings service."""

from llm.services.schema import ProjectConfiguration

import asyncio
from copy import deepcopy
from llm.core.models import ProjectConfig
from .naming import TITLE_ENGINE

from ...config.presets import PresetStore
from ...config.preset_records import PresetDefinition, PresetIndex, PresetProject


class PresetService:
    def __init__(self, settings):
        self.settings = settings
        config = settings.runtime.config
        anchor = config.workspace if config._workspace_anchor is None else config._workspace_anchor
        self.store = PresetStore(anchor)

    async def list(self):
        record = await asyncio.to_thread(self.store.read)
        return PresetIndex(record).selection().to_dict()

    async def export(self, identifier):
        record = await self.settings.load_project(identifier)
        form = ProjectConfiguration.view(record).values
        project = PresetProject.export(form)
        globals_ = await asyncio.to_thread(self.store.globals, self.settings.runtime.config.user_profile)
        return PresetDefinition.create(self.store.VERSION, globals_, project)

    async def save(self, name, value):
        PresetStore.validate(value)
        self.settings._validate(PresetDefinition(value).project.validation_values("Preset"))
        await asyncio.to_thread(self.store.save, name, value)
        return await self.list()

    async def apply(self, name, identifier):
        stored = await asyncio.to_thread(self.store.read)
        value = PresetDefinition(PresetStore.validate(PresetIndex(stored).definition(name)))
        project = await self.settings._project(identifier)
        await self.settings._idle_sessions(project)
        record = await self.settings.load_project(identifier)
        view = ProjectConfiguration.view(record)
        candidate = value.project.overlay(view.values)
        targets = ProjectConfig.from_dict(view.values.config).parameter_targets
        if targets.has_engine(TITLE_ENGINE):
            config = ProjectConfig.from_dict(candidate["config"])
            config.set_engine_parameters(TITLE_ENGINE, targets.engine(TITLE_ENGINE).to_dict())
            candidate["config"] = config.to_dict()
        self._check_fixed(record, candidate)
        requests = [("save_project", (identifier, candidate, view.config_version, view.values.components))]
        requests.extend(("save_global", (section, values)) for section, values in value.globals.items())
        batch = await self.settings.save_all(requests)
        if not batch["error"]:
            await asyncio.to_thread(self.store.select, name)
        return {"batch": batch, "catalog": await self.settings.catalog(), "project": await self.settings.load_project(identifier),
                "presets": await self.list()}

    @staticmethod
    def _check_fixed(record, candidate):
        ProjectConfiguration.validate_edit(record, candidate)
