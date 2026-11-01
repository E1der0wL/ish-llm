"""Public dok settings, lifecycle and optional presentation contracts."""

from .config import ConfigStore, DockConfig, Width, WidthMode
from .audio import AudioConfig, AudioSnapshot, AudioStatus
from .keys import KeySequence
from .player import PlaybackSnapshot, PlaybackStatus
from .records import SettingsError
from .shortcuts import Shortcut


def install(prompt, *, config_path=None):
    # ish executes dok.dok directly before importing its parent package.
    from .dok import install as install_plugin
    return install_plugin(prompt, config_path=config_path)


def uninstall(prompt):
    from .dok import uninstall as uninstall_plugin
    return uninstall_plugin(prompt)

__all__ = [
    "install", "uninstall", "ConfigStore", "DockConfig", "Width", "WidthMode",
    "KeySequence", "Shortcut", "SettingsError", "PlaybackSnapshot", "PlaybackStatus",
    "AudioConfig", "AudioSnapshot", "AudioStatus",
]
