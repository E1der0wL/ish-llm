"""Global Hub preferences shared by projects in one workspace."""

from dataclasses import asdict, replace

from .store import WorkspaceJSONStore
from .profile import UserProfile
from .theme import HubTheme
from .general import GeneralSettings


def configured_profile(config):
    """Resolve persisted identity/language before constructing any UI labels."""
    stored = PreferencesStore(config.workspace).load()
    profile = UserProfile(**stored["profile"]) if "profile" in stored else config.user_profile
    general = GeneralSettings(**stored.get("general", {}))
    packs = {**config.language_packs, **general.language_packs}
    return replace(config, user_profile=profile, language=general.language or profile.language or config.language,
                   language_packs=packs)


class PreferencesStore(WorkspaceJSONStore):
    def __init__(self, workspace):
        super().__init__(workspace, "preferences.json")
        self.sidebar = WorkspaceJSONStore(workspace, "sidebar-width.json")

    def load(self):
        value = self._read_json({"version": 1})
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Invalid Hub preferences")
        result = {}
        if "profile" in value:
            result["profile"] = asdict(UserProfile(**value["profile"]))
        if "appearance" in value:
            result["appearance"] = asdict(HubTheme.from_preferences(value["appearance"]))
        if "general" in value:
            result["general"] = asdict(GeneralSettings(**value["general"]))
        sidebar = self.sidebar._read_json(None)
        if sidebar is not None:
            width = sidebar["sidebar_width"]
            HubTheme(sidebar_width=width)
            result.setdefault("appearance", asdict(HubTheme()))["sidebar_width"] = width
        return result

    def save_sidebar_width(self, width: int) -> int:
        HubTheme(sidebar_width=width)
        self.sidebar._write_json({"sidebar_width": width})
        return width

    def save(self, section, values):
        if section not in ("profile", "appearance", "general"):
            raise ValueError("Unknown preference section")
        record = {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}[section](**values)
        data = {"version": 1, **self.load(), section: asdict(record)}
        if section == "appearance":
            self.save_sidebar_width(record.sidebar_width)
        self._write_json(data)
        return asdict(record)
