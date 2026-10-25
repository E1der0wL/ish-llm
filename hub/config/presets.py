"""Portable settings presets stored once at the Hub launch root."""

from .preferences import PreferencesStore
from .store import WorkspaceJSONStore
from .preset_records import PresetDefinition, PresetIndex, PresetName, PresetGlobals
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


class PresetStore(WorkspaceJSONStore):
    VERSION = PresetDefinition.VERSION
    MAX_NAME_CHARS = PresetName.MAX_CHARS
    FILE = "preset-selection.json"
    DIRECTORY_NAME = "presets"
    SUFFIX = ".json"
    LOCK_FILE = "presets.lock"
    FILE_MODE = 0o600
    GLOB = '*' + SUFFIX
    SYMLINK_ERROR = "Preset files must not be symbolic links"

    def __init__(self, workspace):
        super().__init__(workspace, self.FILE)
        self.definitions = self.directory(workspace) / self.DIRECTORY_NAME

    @classmethod
    def validate(cls, value):
        return PresetDefinition.validate(
            value,
            cls.VERSION,
            PreferencesStore.SECTIONS,
            PreferencesStore.validate_section,
        )

    def read(self):
        if self.definitions.is_symlink():
            raise ValueError(self.SYMLINK_ERROR)
        result = PresetIndex.empty(self.VERSION)
        selection = self._read_json({PresetIndex.SELECTED: PresetIndex.UNSELECTED})
        if not isinstance(selection, dict) or selection.keys() != {PresetIndex.SELECTED}:
            raise ValueError(PresetIndex.INVALID)
        result[PresetIndex.SELECTED] = selection[PresetIndex.SELECTED]
        for path in sorted(self.definitions.glob(self.GLOB)):
            if path.is_symlink():
                raise ValueError(self.SYMLINK_ERROR)
            self.validate_name(path.stem)
            result[PresetIndex.PRESETS][path.stem] = self.validate(
                self._definition(path.stem)._read_json(None)
            )
        return PresetIndex.validate(
            result, self.VERSION, self.validate_name, self.validate
        )

    @classmethod
    def validate_name(cls, name):
        PresetName.validate(name, cls.MAX_NAME_CHARS)

    def _definition(self, name):
        self.validate_name(name)
        return WorkspaceJSONStore(
            self.workspace, Path(self.DIRECTORY_NAME) / (name + self.SUFFIX)
        )

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=self.CREATE_PARENTS, exist_ok=self.EXISTING_DIRECTORY_OK)
        descriptor = os.open(self.path.parent / self.LOCK_FILE,
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, self.FILE_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def save(self, name, value):
        if isinstance(name, str):
            name = name.strip()
        self.validate_name(name)
        value = self.validate(value)
        with self._locked():
            record = self.read()
            PresetIndex(record).add(name, value)
            self._definition(name)._write_json(value)
        return record

    def select(self, name):
        with self._locked():
            record = self.read()
            PresetIndex(record).select(name)
            self._write_json({PresetIndex.SELECTED: name})

    def selected_project(self):
        return PresetIndex(self.read()).selected_project()

    def globals(self, profile=PresetGlobals.DEFAULT_PROFILE):
        return PresetGlobals.snapshot(
            profile, lambda: PreferencesStore(self.workspace).load()
        )
