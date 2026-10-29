"""Current global preference storage and observation envelopes."""

from copy import deepcopy
from dataclasses import asdict, fields

from .general import GeneralSettings
from .profile import UserProfile
from .theme import HubTheme
from .sections import PreferenceSection


class SidebarWidth:
    FILE = "sidebar-width.json"
    ABSENT = None

    @staticmethod
    def observe(value):
        return value[HubTheme.sidebar_width.__name__]

    @staticmethod
    def read(value):
        width = SidebarWidth.observe(value)
        HubTheme(sidebar_width=width)
        return width

    @staticmethod
    def store(value, width):
        value[HubTheme.sidebar_width.__name__] = width

    @staticmethod
    def create(width):
        return {HubTheme.sidebar_width.__name__: width}


class PreferenceSnapshot:
    """Global preference observations; callers retain their input/validation boundary."""

    def __init__(self, value):
        self._value = value

    @property
    def record(self):
        """Borrowed public observation; to_dict remains the detached-copy boundary."""
        return self._value

    def has_section(self, name):
        return name in self._value

    def section(self, name):
        return self._value[name]

    def section_or(self, name, fallback):
        return self._value.get(name, fallback)

    def store_section(self, name, value):
        self._value[name] = value

    def update(self, observation):
        self._value.update(observation.record)

    def prepare_ui(self, initial_appearance):
        self._value.setdefault(
            PreferenceSection.GENERAL.value, asdict(GeneralSettings())
        )
        self._value[PreferenceSection.APPEARANCE.value] = asdict(
            HubTheme(
                **self._value.get(
                    PreferenceSection.APPEARANCE.value, initial_appearance
                )
            )
        )

    def theme_or(self, initial_appearance):
        return HubTheme(
            **self._value.get(PreferenceSection.APPEARANCE.value, initial_appearance)
        )

    def set_sidebar(self, width, initial_appearance):
        appearance = self._value.setdefault(
            PreferenceSection.APPEARANCE.value, initial_appearance
        )
        SidebarWidth.store(appearance, width)

    @classmethod
    def preview(cls, initial_appearance):
        return cls(
            {
                PreferenceSection.PROFILE.value: asdict(UserProfile()),
                PreferenceSection.APPEARANCE.value: initial_appearance.copy(),
                PreferenceSection.GENERAL.value: asdict(GeneralSettings()),
            }
        )

    @property
    def workspace_selection(self):
        from .metadata import SettingMetadata

        return self._value.get(PreferenceSection.GENERAL, {}).get(
            GeneralSettings.workspace_root.__name__,
            SettingMetadata.initial(GeneralSettings, GeneralSettings.workspace_root),
        )

    def profile(self, fallback):
        return (
            UserProfile(**self._value[PreferenceSection.PROFILE])
            if PreferenceSection.PROFILE in self._value
            else fallback
        )

    def general(self):
        return GeneralSettings(**self._value.get(PreferenceSection.GENERAL, {}))

    def required_profile(self):
        return UserProfile(**self._value[PreferenceSection.PROFILE.value])

    def required_theme(self):
        return HubTheme(**self._value[PreferenceSection.APPEARANCE.value])

    def required_general(self):
        return GeneralSettings(**self._value[PreferenceSection.GENERAL.value])

    def theme(self):
        return HubTheme(**self._value.get(PreferenceSection.APPEARANCE, {}))

    @property
    def optional_sidebar_width(self):
        return self._value.get(PreferenceSection.APPEARANCE, {}).get(
            HubTheme.sidebar_width.__name__
        )

    def apply_sidebar(self, width):
        self._value.setdefault(PreferenceSection.APPEARANCE.value, asdict(HubTheme()))[
            HubTheme.sidebar_width.__name__
        ] = width

    def with_section_record(self, version, section, value):
        return {
            PreferenceRecord.VERSION_FIELD: version,
            **self._value,
            section: asdict(value),
        }

    @classmethod
    def catalog(cls, profile, load, workspace):
        from .metadata import SettingMetadata

        values = {
            PreferenceSection.PROFILE.value: asdict(profile),
            PreferenceSection.GENERAL.value: asdict(GeneralSettings()),
            **load(),
        }
        general = values[PreferenceSection.GENERAL]
        if general[GeneralSettings.workspace_root.__name__] == SettingMetadata.initial(
            GeneralSettings, GeneralSettings.workspace_root
        ):
            general[GeneralSettings.workspace_root.__name__] = str(workspace())
        return values

    @classmethod
    def startup(cls, config, general):
        values = asdict(general)
        values[GeneralSettings.workspace_root.__name__] = str(config.workspace)
        return cls(
            {
                PreferenceSection.GENERAL.value: values,
                PreferenceSection.PROFILE.value: asdict(config.user_profile),
            }
        )

    def to_dict(self):
        return deepcopy(self._value)


class PreferenceRecord:
    VERSION = 1
    FILE = "preferences.json"
    VERSION_FIELD = "version"
    INVALID = "Invalid Hub preferences"
    INCOMPLETE_SECTION = (
        "Stored {section} must contain exactly the current preference fields"
    )
    UNKNOWN_SECTION = "Unknown preference section"
    SECTIONS = {
        PreferenceSection.PROFILE.value: UserProfile,
        PreferenceSection.APPEARANCE.value: HubTheme,
        PreferenceSection.GENERAL.value: GeneralSettings,
    }

    @classmethod
    def empty(cls, version):
        return {cls.VERSION_FIELD: version}

    @classmethod
    def validate_section(cls, owner, section, record):
        expected = {item.name for item in fields(owner)}
        # Explicit additive fields use their Owner defaults when omitted.
        # Removed/unknown fields are still rejected, never silently migrated.
        optional = {
            HubTheme: {HubTheme.comment2.__name__, HubTheme.accent4.__name__, HubTheme.accent5.__name__},
            GeneralSettings: {GeneralSettings.tool_context_chars.__name__},
        }.get(owner, set())
        if not isinstance(record, dict) or not expected - optional <= record.keys() <= expected:
            raise TypeError(cls.INCOMPLETE_SECTION.format(section=section))
        return asdict(owner(**record))

    @classmethod
    def read(cls, value, version, sections, validate_section):
        if (
            not isinstance(value, dict)
            or type(value.get(cls.VERSION_FIELD)) is not int
            or value[cls.VERSION_FIELD] != version
            or value.keys() - sections.keys() - {cls.VERSION_FIELD}
        ):
            raise ValueError(cls.INVALID)
        result = {}
        for section in sections:
            if section in value:
                result[section] = validate_section(section, value[section])
        return PreferenceSnapshot(result)
