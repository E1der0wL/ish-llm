"""Global Hub preferences anchored to the installation's initial workspace."""

from dataclasses import asdict, replace

from .store import WorkspaceJSONStore
from .profile import UserProfile
from .theme import HubTheme
from .general import GeneralSettings
from .sections import PreferenceSection
from .workspace import WorkspaceSelection
from .preference_records import PreferenceRecord, PreferenceSnapshot, SidebarWidth


def configured_workspace(config):
    """Resolve once at startup; later saves only affect the next installation.

    Keep preferences at the original launch root so selecting A -> B -> C
    never requires redirect chains or loses global preferences on an empty root.
    """
    if config._workspace_anchor is not WorkspaceSelection.UNBOUND_ANCHOR:
        return config
    from pathlib import Path
    from .workspace import workspace_path

    anchor = Path(config.workspace).expanduser().absolute()
    stored = PreferencesStore(anchor).snapshot()
    selected = workspace_path(stored.workspace_selection)
    return replace(config, workspace=selected or anchor, _workspace_anchor=anchor)


def configured_profile(config):
    """Resolve persisted identity/language before constructing any UI labels."""
    config = configured_workspace(config)
    stored = PreferencesStore(config._workspace_anchor).snapshot()
    profile = stored.profile(config.user_profile)
    general = stored.general()
    packs = {**config.language_packs, **general.language_packs}
    language = general.language
    if language == GeneralSettings.AUTO_LANGUAGE:
        language = (
            config.language
            if profile.language == UserProfile.AUTO_LANGUAGE
            else profile.language
        )
    return replace(
        config, user_profile=profile, language=language, language_packs=packs
    )


class PreferencesStore(WorkspaceJSONStore):
    VERSION = PreferenceRecord.VERSION
    SECTIONS = PreferenceRecord.SECTIONS

    def __init__(self, workspace):
        super().__init__(workspace, PreferenceRecord.FILE)
        self.sidebar = WorkspaceJSONStore(workspace, SidebarWidth.FILE)

    @classmethod
    def validate_section(cls, section, record):
        """Validate a complete current section, including portable presets."""
        return PreferenceRecord.validate_section(cls.SECTIONS[section], section, record)

    def snapshot(self):
        value = self._read_json(PreferenceRecord.empty(self.VERSION))
        result = PreferenceRecord.read(
            value, self.VERSION, self.SECTIONS, self.validate_section
        )
        sidebar = self.sidebar._read_json(SidebarWidth.ABSENT)
        if sidebar is not SidebarWidth.ABSENT:
            result.apply_sidebar(SidebarWidth.read(sidebar))
        return result

    def load(self):
        return self.snapshot().to_dict()

    def save_sidebar_width(self, width: int) -> int:
        HubTheme(sidebar_width=width)
        self.sidebar._write_json(SidebarWidth.create(width))
        return width

    @classmethod
    def input_record(cls, section, values):
        return cls.SECTIONS[section](**values)

    def save(self, section, values):
        if section not in self.SECTIONS:
            raise ValueError(PreferenceRecord.UNKNOWN_SECTION)
        # Creation/edit input may omit owner-defined initial values. The saved
        # section is complete; load never fills missing stored fields.
        record = self.input_record(section, values)
        data = PreferenceSnapshot(self.load()).with_section_record(
            self.VERSION, section, record
        )
        if section == PreferenceSection.APPEARANCE:
            self.save_sidebar_width(record.sidebar_width)
        self._write_json(data)
        return asdict(record)
