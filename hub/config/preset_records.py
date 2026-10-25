"""The sole current portable Preset and saved selection record contracts."""

from copy import deepcopy
from dataclasses import asdict, dataclass

from llm.core.models import ConversationStorage, ProjectConfig
from llm.core.agents import AgentDefinition
from llm.services.configuration_records import ProjectConfigForm
from ..backend.project.title_contract import TitleEngineIdentity
from ..backend.execution.engine_selection import HubEngineIdentity


class PresetGlobals:
    DEFAULT_PROFILE = None

    @classmethod
    def snapshot(cls, profile, load):
        from .defaults import HubDefaults
        values = HubDefaults.globals(profile)
        for section, saved in load().items():
            values[section].update(saved)
        return values


class PresetProject:
    CONFIG = ProjectConfigForm.config.__name__
    COMPONENTS = ProjectConfigForm.components.__name__
    STORAGE = ProjectConfigForm.conversation_storage.__name__
    AGENTS = "agents"
    FIELDS = {CONFIG, COMPONENTS, STORAGE}
    STORAGE_NULLABLE = False
    INVALID_FIELDS = (
        "Preset project requires config, components and conversation_storage"
    )
    INVALID_SETTINGS = "Invalid project settings"
    INVALID_COMPONENTS = "Preset components must be distinct names"
    MANAGED_ENGINE = "_hub_title is managed by Hub"
    INVALID_AGENTS = "Preset agents must be a definition map"

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or not cls.FIELDS <= value.keys() <= cls.FIELDS | {cls.AGENTS}:
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
        agents = value.get(cls.AGENTS, {})
        if not isinstance(agents, dict):
            raise ValueError(cls.INVALID_AGENTS)
        for identifier, definition in agents.items():
            if HubEngineIdentity.internal(identifier):
                raise ValueError(cls.MANAGED_ENGINE)
            AgentDefinition.validate(identifier, definition)

    @classmethod
    def export(cls, form):
        result = form.to_dict()
        del result[ProjectConfigForm.title.__name__]
        result[cls.CONFIG] = ProjectConfig.without_engine_parameters(
            result[cls.CONFIG], TitleEngineIdentity.NAME
        )
        return cls(result)

    @property
    def config(self):
        return self._value[self.CONFIG]

    @property
    def components(self):
        return self._value[self.COMPONENTS]

    @property
    def conversation_storage(self):
        return self._value[self.STORAGE]

    def validation_values(self, title):
        return {ProjectConfigForm.title.__name__: title,
                **{key: self._value[key] for key in self.FIELDS}}

    def overlay(self, form):
        return {**form.to_dict(), **{key: deepcopy(self._value[key]) for key in self.FIELDS}}

    @property
    def agents(self):
        return deepcopy(self._value.get(self.AGENTS, {}))

    def with_agents(self, agents):
        return type(self)({**self.to_dict(), self.AGENTS: deepcopy(agents)})

    def application_values(self, form):
        candidate = self.overlay(form)
        targets = ProjectConfig.from_dict(form.config).parameter_targets
        if targets.has_engine(TitleEngineIdentity.NAME):
            config = ProjectConfig.from_dict(candidate[self.CONFIG])
            config.set_engine_parameters(TitleEngineIdentity.NAME, targets.engine(TitleEngineIdentity.NAME).to_dict())
            candidate[self.CONFIG] = config.to_dict()
        return candidate

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

    @classmethod
    def from_record(cls, value):
        return cls(value[cls.names.__name__], value[cls.selected.__name__])

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
    FORBIDDEN = frozenset('/\\<>:"|?*')
    RESERVED = frozenset(('CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))))
    EXTENSION_SEPARATOR = '.'
    STEM_POSITION = 0
    INVALID = "Preset name must contain 1–{maximum} printable characters"

    @classmethod
    def validate(cls, name, maximum):
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or len(name) > maximum
            or any(ord(char) < cls.PRINTABLE_START for char in name)
            or any(char in cls.FORBIDDEN for char in name)
            or name.endswith(cls.EXTENSION_SEPARATOR)
            or name.split(cls.EXTENSION_SEPARATOR)[cls.STEM_POSITION].upper() in cls.RESERVED
        ):
            raise ValueError(cls.INVALID.format(maximum=maximum))
