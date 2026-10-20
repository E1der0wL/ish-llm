"""Global Hub preferences anchored to the installation's initial workspace."""

from dataclasses import asdict, fields, replace

from .store import WorkspaceJSONStore
from .profile import UserProfile
from .theme import HubTheme
from .general import GeneralSettings


def configured_workspace(config):
    """Resolve once at startup; later saves only affect the next installation.

    Keep preferences at the original launch root so selecting A -> B -> C
    never requires redirect chains or loses global preferences on an empty root.
    """
    if config._workspace_anchor is not None:
        return config
    from pathlib import Path
    from .workspace import workspace_path
    anchor = Path(config.workspace).expanduser().absolute()
    stored = PreferencesStore(anchor).load()
    selected = workspace_path(stored.get("general", {}).get("workspace_root", ""))
    return replace(config, workspace=selected or anchor, _workspace_anchor=anchor)


def configured_profile(config):
    """Resolve persisted identity/language before constructing any UI labels."""
    config = configured_workspace(config)
    stored = PreferencesStore(config._workspace_anchor).load()
    profile = UserProfile(**stored["profile"]) if "profile" in stored else config.user_profile
    general = GeneralSettings(**stored.get("general", {}))
    packs = {**config.language_packs, **general.language_packs}
    return replace(config, user_profile=profile, language=general.language or profile.language or config.language,
                   language_packs=packs)


class PreferencesStore(WorkspaceJSONStore):
    VERSION = 1
    SECTIONS = {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}

    def __init__(self, workspace):
        super().__init__(workspace, "preferences.json")
        self.sidebar = WorkspaceJSONStore(workspace, "sidebar-width.json")

    def load(self):
        value = self._read_json({"version": self.VERSION})
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != self.VERSION or value.keys() - self.SECTIONS.keys() - {"version"}):
            raise ValueError("Invalid Hub preferences")
        result = {}
        for section, owner in self.SECTIONS.items():
            if section in value:
                record = value[section]
                if not isinstance(record, dict) or record.keys() != {field.name for field in fields(owner)}:
                    raise TypeError(f"Stored {section} must contain exactly the current preference fields")
                result[section] = asdict(owner(**record))
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
        if section not in self.SECTIONS:
            raise ValueError("Unknown preference section")
        # Creation/edit input may omit owner-defined initial values. The saved
        # section is complete; load never fills missing stored fields.
        record = self.SECTIONS[section](**values)
        data = {"version": self.VERSION, **self.load(), section: asdict(record)}
        if section == "appearance":
            self.save_sidebar_width(record.sidebar_width)
        self._write_json(data)
        return asdict(record)
