"""Portable settings presets stored once at the Hub launch root."""

from copy import deepcopy
from dataclasses import asdict

from llm.core.models import ProjectConfig

from .general import GeneralSettings
from .preferences import PreferencesStore
from .profile import UserProfile
from .store import WorkspaceJSONStore
from .theme import HubTheme


class PresetStore(WorkspaceJSONStore):
    VERSION = 1
    MAX_NAME_CHARS = 80

    def __init__(self, workspace):
        super().__init__(workspace, "presets.json")

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or set(value) != {"version", "global", "project"}:
            raise ValueError("Preset requires version, global and project")
        if type(value["version"]) is not int or value["version"] != cls.VERSION:
            raise ValueError("Unsupported preset version")
        globals_ = value["global"]
        if not isinstance(globals_, dict) or globals_.keys() != PreferencesStore.SECTIONS.keys():
            raise ValueError("Preset must include profile, appearance and general settings")
        for name, record in globals_.items():
            PreferencesStore.validate_section(name, record)
        project = value["project"]
        if not isinstance(project, dict) or set(project) != {"config", "components", "conversation_storage"}:
            raise ValueError("Preset project requires config, components and conversation_storage")
        if not isinstance(project["config"], dict) or not isinstance(project["components"], list):
            raise ValueError("Invalid project settings")
        ProjectConfig.from_dict(project["config"])
        components = project["components"]
        if (any(not isinstance(name, str) or not name for name in components)
                or len(set(components)) != len(components)):
            raise ValueError("Preset components must be distinct names")
        if project["conversation_storage"] not in ("file", "memory"):
            raise ValueError("Invalid conversation storage")
        parameters = project["config"].get("parameters", {})
        if not isinstance(parameters, dict) or not isinstance(parameters.get("engines", {}), dict):
            raise ValueError("Project parameters and engines must be JSON objects")
        if "_hub_title" in parameters.get("engines", {}):
            raise ValueError("_hub_title is managed by Hub")
        return deepcopy(value)

    def read(self):
        result = self._read_json({"version": self.VERSION, "selected": "", "presets": {}})
        if not isinstance(result, dict) or set(result) != {"version", "selected", "presets"}:
            raise ValueError("Invalid preset store")
        if type(result["version"]) is not int or result["version"] != self.VERSION:
            raise ValueError("Unsupported preset store version")
        if not isinstance(result["presets"], dict) or not isinstance(result["selected"], str):
            raise ValueError("Invalid preset store")
        for name, value in result["presets"].items():
            self.validate_name(name)
            self.validate(value)
        if result["selected"] and result["selected"] not in result["presets"]:
            raise ValueError("Selected preset is missing")
        return result

    @classmethod
    def validate_name(cls, name):
        if (not isinstance(name, str) or not name or name != name.strip()
                or len(name) > cls.MAX_NAME_CHARS or any(ord(char) < 32 for char in name)):
            raise ValueError(f"Preset name must contain 1–{cls.MAX_NAME_CHARS} printable characters")

    def save(self, name, value):
        if isinstance(name, str):
            name = name.strip()
        self.validate_name(name)
        value = self.validate(value)
        record = self.read()
        if name in record["presets"]:
            raise ValueError("A preset with this name already exists")
        record["presets"][name] = value
        self._write_json(record)
        return record

    def select(self, name):
        record = self.read()
        if name not in record["presets"]:
            raise ValueError("Unknown preset")
        record["selected"] = name
        self._write_json(record)

    def selected_project(self):
        record = self.read()
        return record["presets"][record["selected"]]["project"] if record["selected"] else None

    def globals(self, profile=None):
        return {"profile": asdict(UserProfile() if profile is None else profile), "appearance": asdict(HubTheme()),
                "general": asdict(GeneralSettings()), **PreferencesStore(self.path.parent.parent).load()}
