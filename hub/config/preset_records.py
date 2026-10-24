"""The sole current portable Preset and saved selection record contracts."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from llm.core.models import ConversationStorage, ProjectConfig
from llm.services.configuration_records import ProjectConfigForm
from ..backend.project.title_contract import TitleEngineIdentity


class PresetProject:
    CONFIG = ProjectConfigForm.config.__name__
    COMPONENTS = ProjectConfigForm.components.__name__
    STORAGE = ProjectConfigForm.conversation_storage.__name__
    FIELDS = {CONFIG, COMPONENTS, STORAGE}
    STORAGE_NULLABLE = False
    INVALID_FIELDS = (
        "Preset project requires config, components and conversation_storage"
    )
    INVALID_SETTINGS = "Invalid project settings"
    INVALID_COMPONENTS = "Preset components must be distinct names"
    MANAGED_ENGINE = "_hub_title is managed by Hub"

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or set(value) != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if not isinstance(value[cls.CONFIG], dict) or not isinstance(
            value[cls.COMPONENTS], list
        ):
            raise ValueError(cls.INVALID_SETTINGS)
        config = ProjectConfig.from_dict(value[cls.CONFIG])
        components = value[cls.COMPONENTS]
        if any(not isinstance(name, str) or not name for name in components) or len(
            set(components)
        ) != len(components):
            raise ValueError(cls.INVALID_COMPONENTS)
        ConversationStorage.validate(value[cls.STORAGE], nullable=cls.STORAGE_NULLABLE)
        if config.parameter_targets.has_engine(TitleEngineIdentity.NAME):
            raise ValueError(cls.MANAGED_ENGINE)

    @classmethod
    def export(cls, form):
        result = form.to_dict()
        del result[ProjectConfigForm.title.__name__]
        result[cls.CONFIG] = ProjectConfig.without_engine_parameters(
            result[cls.CONFIG], TitleEngineIdentity.NAME
        )
        return cls(result)

    def validation_values(self, title):
        return {ProjectConfigForm.title.__name__: title, **self._value}

    def overlay(self, form):
        return {**form.to_dict(), **deepcopy(self._value)}

    def to_dict(self):
        return deepcopy(self._value)


class PresetDefinition:
    VERSION = 1
    VERSION_FIELD = "version"
    GLOBAL = "global"
    PROJECT = "project"
    FIELDS = {VERSION_FIELD, GLOBAL, PROJECT}
    INVALID_FIELDS = "Preset requires version, global and project"
    INVALID_VERSION = "Unsupported preset version"
    INVALID_GLOBALS = "Preset must include profile, appearance and general settings"

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value, version, sections, validate_section):
        if not isinstance(value, dict) or set(value) != cls.FIELDS:
            raise ValueError(cls.INVALID_FIELDS)
        if (
            type(value[cls.VERSION_FIELD]) is not int
            or value[cls.VERSION_FIELD] != version
        ):
            raise ValueError(cls.INVALID_VERSION)
        globals_ = value[cls.GLOBAL]
        if not isinstance(globals_, dict) or globals_.keys() != sections.keys():
            raise ValueError(cls.INVALID_GLOBALS)
        for name, record in globals_.items():
            validate_section(name, record)
        PresetProject.validate(value[cls.PROJECT])
        return deepcopy(value)

    @classmethod
    def create(cls, version, globals_, project):
        return {
            cls.VERSION_FIELD: version,
            cls.GLOBAL: globals_,
            cls.PROJECT: project.to_dict(),
        }

    @property
    def project(self):
        return PresetProject(self._value[self.PROJECT])

    @property
    def globals(self):
        return self._value[self.GLOBAL]


@dataclass(frozen=True, slots=True)
class PresetSelection:
    names: list[str]
    selected: str

    def to_dict(self):
        return asdict(self)


class PresetIndex:
    VERSION_FIELD = PresetDefinition.VERSION_FIELD
    SELECTED = PresetSelection.selected.__name__
    PRESETS = "presets"
    FIELDS = {VERSION_FIELD, SELECTED, PRESETS}
    UNSELECTED = ""
    NO_PROJECT = None
    INVALID = "Invalid preset store"
    INVALID_VERSION = "Unsupported preset store version"
    MISSING_SELECTED = "Selected preset is missing"
    DUPLICATE = "A preset with this name already exists"
    UNKNOWN = "Unknown preset"

    def __init__(self, value):
        self._value = value

    @classmethod
    def empty(cls, version):
        return {
            cls.VERSION_FIELD: version,
            cls.SELECTED: cls.UNSELECTED,
            cls.PRESETS: {},
        }

    @classmethod
    def validate(cls, value, version, validate_name, validate_definition):
        if not isinstance(value, dict) or set(value) != cls.FIELDS:
            raise ValueError(cls.INVALID)
        if (
            type(value[cls.VERSION_FIELD]) is not int
            or value[cls.VERSION_FIELD] != version
        ):
            raise ValueError(cls.INVALID_VERSION)
        if not isinstance(value[cls.PRESETS], dict) or not isinstance(
            value[cls.SELECTED], str
        ):
            raise ValueError(cls.INVALID)
        for name, definition in value[cls.PRESETS].items():
            validate_name(name)
            validate_definition(definition)
        if value[cls.SELECTED] and value[cls.SELECTED] not in value[cls.PRESETS]:
            raise ValueError(cls.MISSING_SELECTED)
        return value

    def add(self, name, value):
        if name in self._value[self.PRESETS]:
            raise ValueError(self.DUPLICATE)
        self._value[self.PRESETS][name] = value

    def select(self, name):
        if name not in self._value[self.PRESETS]:
            raise ValueError(self.UNKNOWN)
        self._value[self.SELECTED] = name

    def definition(self, name):
        return self._value[self.PRESETS][name]

    def selected_project(self):
        return (
            self._value[self.PRESETS][self._value[self.SELECTED]][
                PresetDefinition.PROJECT
            ]
            if self._value[self.SELECTED]
            else self.NO_PROJECT
        )

    def selection(self):
        return PresetSelection(
            list(self._value[self.PRESETS]),
            self._value.get(self.SELECTED, self.UNSELECTED),
        )


class PresetName:
    MAX_CHARS = 80
    PRINTABLE_START = 32
    INVALID = "Preset name must contain 1–{maximum} printable characters"

    @classmethod
    def validate(cls, name, maximum):
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or len(name) > maximum
            or any(ord(char) < cls.PRINTABLE_START for char in name)
        ):
            raise ValueError(cls.INVALID.format(maximum=maximum))
