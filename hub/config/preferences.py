"""Global Hub preferences shared by projects in one workspace."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile

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


class PreferencesStore:
    def __init__(self, workspace):
        self.path = Path(workspace).expanduser() / ".hub" / "preferences.json"

    def load(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Invalid Hub preferences")
        result = {}
        if "profile" in value:
            result["profile"] = asdict(UserProfile(**value["profile"]))
        if "appearance" in value:
            result["appearance"] = asdict(HubTheme.from_preferences(value["appearance"]))
        if "general" in value:
            result["general"] = asdict(GeneralSettings(**value["general"]))
        return result

    def save(self, section, values):
        if section not in ("profile", "appearance", "general"):
            raise ValueError("Unknown preference section")
        record = {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}[section](**values)
        data = {"version": 1, **self.load(), section: asdict(record)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="preferences-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return asdict(record)
