"""Apply metadata for global settings; metadata is never a user setting."""

from dataclasses import asdict, replace

from .general import GeneralSettings
from .profile import UserProfile
from .theme import HubTheme
from .workspace import WorkspaceSelection, workspace_path
from .preferences import PreferencesStore
from .metadata import SettingMetadata
from .preference_records import PreferenceSnapshot


def setting_metadata(section):
    return SettingMetadata.describe(PreferencesStore.SECTIONS[section])


def restart_changes(
    section, saved, applied, *, anchor=WorkspaceSelection.UNBOUND_ANCHOR
):
    """Compare successfully saved values with this instance's startup values."""
    changed = []
    for name in SettingMetadata.restart_fields(PreferencesStore.SECTIONS[section]):
        before, after = applied.get(name), saved.get(name)
        if name == GeneralSettings.workspace_root.__name__:
            before = (
                workspace_path(
                    before
                    or SettingMetadata.initial(
                        GeneralSettings, GeneralSettings.workspace_root
                    )
                )
                or anchor
            )
            after = (
                workspace_path(
                    after
                    or SettingMetadata.initial(
                        GeneralSettings, GeneralSettings.workspace_root
                    )
                )
                or anchor
            )
        if before != after:
            changed.append(SettingMetadata.path(section, name))
    return changed


def startup_settings(config, general):
    return PreferenceSnapshot.startup(config, general).to_dict()


def restart_config(launch_config, current_config):
    from .preferences import configured_profile

    anchor = (
        launch_config.workspace
        if current_config._workspace_anchor is WorkspaceSelection.UNBOUND_ANCHOR
        else current_config._workspace_anchor
    )
    config = configured_profile(
        replace(
            launch_config,
            workspace=anchor,
            _workspace_anchor=WorkspaceSelection.UNBOUND_ANCHOR,
        )
    )
    if config.workspace != current_config.workspace:
        config = replace(
            config,
            project_id=WorkspaceSelection.NO_PROJECT,
            session_id=WorkspaceSelection.NO_SESSION,
        )
    return config
