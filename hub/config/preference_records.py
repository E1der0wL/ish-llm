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
    def read(value):
        width = value[HubTheme.sidebar_width.__name__]
        HubTheme(sidebar_width=width)
        return width

    @staticmethod
    def create(width):
        return {HubTheme.sidebar_width.__name__: width}


class PreferenceSnapshot:
    """A freshly read, validated selection; absent initial sections stay absent."""

    def __init__(self, value):
        self._value = value

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
        if not isinstance(record, dict) or record.keys() != {
            item.name for item in fields(owner)
        }:
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
