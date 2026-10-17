"""Preset persistence and application through the existing settings service."""

import asyncio
import json
from copy import deepcopy

from ...config.presets import PresetStore


class PresetService:
    def __init__(self, settings):
        self.settings = settings
        config = settings.runtime.config
        self.store = PresetStore(config._workspace_anchor or config.workspace)

    async def list(self):
        record = await asyncio.to_thread(self.store.read)
        return {"names": list(record["presets"]), "selected": record.get("selected", "")}

    async def export(self, identifier):
        record = await self.settings.load_project(identifier)
        project = {key: deepcopy(record["values"][key]) for key in ("config", "components", "conversation_storage")}
        project["config"].get("parameters", {}).get("engines", {}).pop("_hub_title", None)
        return {"version": 1, "global": await asyncio.to_thread(self.store.globals, self.settings.runtime.config.user_profile),
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
        candidate = {**record["values"], **deepcopy(value["project"])}
        hidden = record["values"]["config"].get("parameters", {}).get("engines", {}).get("_hub_title")
        if hidden is not None:
            candidate["config"].setdefault("parameters", {}).setdefault("engines", {})["_hub_title"] = deepcopy(hidden)
        self._check_fixed(record, candidate)
        requests = [("save_project", (identifier, candidate, record["config_version"], record["values"]["components"]))]
        requests.extend(("save_global", (section, values)) for section, values in value["global"].items())
        batch = await self.settings.save_all(requests)
        if not batch["error"]:
            await asyncio.to_thread(self.store.select, name)
        return {"batch": batch, "catalog": await self.settings.catalog(), "project": await self.settings.load_project(identifier),
                "presets": await self.list()}

    @staticmethod
    def _check_fixed(record, candidate):
        # Effective settings are observations. Never materialize host-fixed
        # leaves by importing a preset from another project.
        missing = object()
        def at(value, path):
            for part in path:
                if not isinstance(value, dict) or part not in value:
                    return missing
                value = value[part]
            return value
        fixed = set()
        def schema_locks(schema, path=()):
            if not isinstance(schema, dict):
                return
            if schema.get("x-host-override"):
                fixed.add(path)
            for key, child in schema.get("properties", {}).items():
                schema_locks(child, (*path, key))
            for child in schema.get("allOf", ()):
                schema_locks(child, path)
        schema_locks(record.get("schema", {}))
        engine_keys = record.get("schema", {}).get("x-engines", {})
        engines = [(engine_keys.get(name, {}).get("configuration_key", name), view)
                   for name, view in record.get("effective_engines", {}).items()]
        components = [(name, spec.get("effective") or {}) for name, spec in record.get("components", {}).items()]
        for kind, entries in (("engines", engines), ("components", components)):
            for name, view in entries:
                for pointer, editable in view.get("editable", {}).items():
                    if editable:
                        continue
                    path = ("config", "parameters", kind, name,
                            *(part.replace("~1", "/").replace("~0", "~") for part in pointer.lstrip("/").split("/")))
                    fixed.add(path)
        for path in fixed:
            before, after = at(record["values"], path), at(candidate, path)
            if before is missing and after is missing:
                continue
            if (before is missing or after is missing or
                    json.dumps(before, sort_keys=True, allow_nan=False) != json.dumps(after, sort_keys=True, allow_nan=False)):
                raise ValueError("Host-fixed setting: " + ".".join(path))
