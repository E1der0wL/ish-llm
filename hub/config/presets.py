"""Portable settings presets stored once at the Hub launch root."""

from copy import deepcopy
from dataclasses import asdict

from .general import GeneralSettings
from .preferences import PreferencesStore
from .profile import UserProfile
from .store import WorkspaceJSONStore
from .theme import HubTheme


class PresetStore(WorkspaceJSONStore):
    def __init__(self, workspace):
        super().__init__(workspace, "presets.json")

    @staticmethod
    def validate(value):
        if (not isinstance(value, dict) or set(value) != {"version", "global", "project"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError("Preset requires version=1, global and project")
        globals_ = value["global"]
        types = {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}
        if not isinstance(globals_, dict) or set(globals_) != set(types):
            raise ValueError("Preset must include profile, appearance and general settings")
        for name, kind in types.items():
            kind(**globals_[name])
        project = value["project"]
        if not isinstance(project, dict) or set(project) != {"config", "components", "conversation_storage"}:
            raise ValueError("Preset project requires config, components and conversation_storage")
        if not isinstance(project["config"], dict) or not isinstance(project["components"], list):
            raise ValueError("Invalid project settings")
        if project["conversation_storage"] not in ("file", "memory"):
            raise ValueError("Invalid conversation storage")
        parameters = project["config"].get("parameters", {})
        if not isinstance(parameters, dict) or not isinstance(parameters.get("engines", {}), dict):
            raise ValueError("Project parameters and engines must be JSON objects")
        if "_hub_title" in parameters.get("engines", {}):
            raise ValueError("_hub_title is managed by Hub")
        return deepcopy(value)

    def read(self):
        result = self._read_json({"version": 1, "selected": "", "presets": {}})
        if (not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1
                or not isinstance(result.get("presets"), dict) or not isinstance(result.get("selected"), str)
                or not all(isinstance(name, str) and name.strip() for name in result["presets"])):
            raise ValueError("Invalid preset store")
        return result

    def save(self, name, value):
        name = name.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise ValueError("Preset name must contain 1–80 printable characters")
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
        value = record["presets"].get(record.get("selected"))
        return self.validate(value)["project"] if value is not None else None

    def globals(self, profile=None):
        return {"profile": asdict(profile or UserProfile()), "appearance": asdict(HubTheme()),
                "general": asdict(GeneralSettings()), **PreferencesStore(self.path.parent.parent).load()}
