"""Preset persistence and application through the existing settings service."""

from llm.services.schema import ProjectConfiguration

import asyncio
from copy import deepcopy
from llm.core.models import ProjectConfig
from .naming import TITLE_ENGINE

from ...config.presets import PresetStore


class PresetService:
    def __init__(self, settings):
        self.settings = settings
        config = settings.runtime.config
        anchor = config.workspace if config._workspace_anchor is None else config._workspace_anchor
        self.store = PresetStore(anchor)

    async def list(self):
        record = await asyncio.to_thread(self.store.read)
        return {"names": list(record["presets"]), "selected": record.get("selected", "")}

    async def export(self, identifier):
        record = await self.settings.load_project(identifier)
        form = ProjectConfiguration.view(record).values
        project = form.to_dict()
        del project["title"]
        project["config"] = ProjectConfig.without_engine_parameters(project["config"], TITLE_ENGINE)
        return {"version": self.store.VERSION, "global": await asyncio.to_thread(self.store.globals, self.settings.runtime.config.user_profile),
                "project": project}

    async def save(self, name, value):
        PresetStore.validate(value)
        self.settings._validate({"title": "Preset", **value["project"]})
        await asyncio.to_thread(self.store.save, name, value)
        return await self.list()

    async def apply(self, name, identifier):
        stored = await asyncio.to_thread(self.store.read)
        value = PresetStore.validate(stored["presets"][name])
        project = await self.settings._project(identifier)
        await self.settings._idle_sessions(project)
        record = await self.settings.load_project(identifier)
        view = ProjectConfiguration.view(record)
        candidate = {**view.values.to_dict(), **deepcopy(value["project"])}
        targets = ProjectConfig.from_dict(view.values.config).parameter_targets
        if targets.has_engine(TITLE_ENGINE):
            config = ProjectConfig.from_dict(candidate["config"])
            config.set_engine_parameters(TITLE_ENGINE, targets.engine(TITLE_ENGINE).to_dict())
            candidate["config"] = config.to_dict()
        self._check_fixed(record, candidate)
        requests = [("save_project", (identifier, candidate, view.config_version, view.values.components))]
        requests.extend(("save_global", (section, values)) for section, values in value["global"].items())
        batch = await self.settings.save_all(requests)
        if not batch["error"]:
            await asyncio.to_thread(self.store.select, name)
        return {"batch": batch, "catalog": await self.settings.catalog(), "project": await self.settings.load_project(identifier),
                "presets": await self.list()}

    @staticmethod
    def _check_fixed(record, candidate):
        ProjectConfiguration.validate_edit(record, candidate)
