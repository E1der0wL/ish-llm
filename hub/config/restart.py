"""Apply metadata for global settings; metadata is never a user setting."""

from dataclasses import asdict, fields, replace

from .general import GeneralSettings
from .profile import UserProfile
from .theme import HubTheme
from .workspace import workspace_path


def setting_metadata(section):
    model = {"general": GeneralSettings, "profile": UserProfile, "appearance": HubTheme}[section]
    return {field.name: {"requires_restart": bool(field.metadata.get("requires_restart", False))}
            for field in fields(model)}


def restart_changes(section, saved, applied, *, anchor=None):
    """Compare successfully saved values with this instance's startup values."""
    changed = []
    for name, metadata in setting_metadata(section).items():
        if not metadata["requires_restart"]:
            continue
        before, after = applied.get(name), saved.get(name)
        if name == "workspace_root":
            before = workspace_path(before or "") or anchor
            after = workspace_path(after or "") or anchor
        if before != after:
            changed.append(section + "." + name)
    return changed


def startup_settings(config, general):
    values = asdict(general)
    values["workspace_root"] = str(config.workspace)
    return {"general": values, "profile": asdict(config.user_profile)}


def restart_config(launch_config, current_config):
    from .preferences import configured_profile
    anchor = launch_config.workspace if current_config._workspace_anchor is None else current_config._workspace_anchor
    config = configured_profile(replace(launch_config,
        workspace=anchor, _workspace_anchor=None))
    if config.workspace != current_config.workspace:
        config = replace(config, project_id=None, session_id=None)
    return config
