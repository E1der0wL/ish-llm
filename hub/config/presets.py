"""Portable settings presets stored once at the Hub launch root."""

from .preferences import PreferencesStore
from .store import WorkspaceJSONStore
from .preset_records import PresetDefinition, PresetIndex, PresetName, PresetGlobals


class PresetStore(WorkspaceJSONStore):
    VERSION = PresetDefinition.VERSION
    MAX_NAME_CHARS = PresetName.MAX_CHARS
    FILE = "presets.json"

    def __init__(self, workspace):
        super().__init__(workspace, self.FILE)

    @classmethod
    def validate(cls, value):
        return PresetDefinition.validate(
            value,
            cls.VERSION,
            PreferencesStore.SECTIONS,
            PreferencesStore.validate_section,
        )

    def read(self):
        result = self._read_json(PresetIndex.empty(self.VERSION))
        return PresetIndex.validate(
            result, self.VERSION, self.validate_name, self.validate
        )

    @classmethod
    def validate_name(cls, name):
        PresetName.validate(name, cls.MAX_NAME_CHARS)

    def save(self, name, value):
        if isinstance(name, str):
            name = name.strip()
        self.validate_name(name)
        value = self.validate(value)
        record = self.read()
        PresetIndex(record).add(name, value)
        self._write_json(record)
        return record

    def select(self, name):
        record = self.read()
        PresetIndex(record).select(name)
        self._write_json(record)

    def selected_project(self):
        return PresetIndex(self.read()).selected_project()

    def globals(self, profile=PresetGlobals.DEFAULT_PROFILE):
        return PresetGlobals.snapshot(
            profile, lambda: PreferencesStore(self.workspace).load()
        )
