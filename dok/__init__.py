"""Public dok settings, lifecycle and optional presentation contracts."""

from .core.elements import ElementConfig, Spacing
from .core.styles import TextStyle, BoldStyle
from .core.records import SettingsError
from .core.state import DockMode
from .dashboard.config import WidgetsConfig
from .features.audio.config import AudioConfig, VisualizerConfig
from .features.audio.models import AudioSnapshot, AudioStatus
from .features.clock.config import ClockConfig, DateConfig, TimeConfig, WeekdayConfig
from .features.media.config import ProgressConfig, TitleConfig, TransportButton, TransportConfig
from .features.media.models import PlaybackAction, PlaybackSnapshot, PlaybackStatus
from .features.shortcuts.config import Separator, Shortcut, ShortcutsConfig
from .features.weather.config import WeatherConfig, WeatherLocation, WeatherIcons
from .features.weather.models import TemperatureUnit, WeatherSnapshot
from .integrations.ish.keys import KeySequence
from .settings.config import DockConfig, DockSettings, DockKeys, Width, WidthMode
from .settings.store import ConfigStore


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
    "VisualizerConfig", "Separator",
    "TitleConfig", "ProgressConfig", "TransportConfig", "TransportButton", "PlaybackAction", "ShortcutsConfig",
    "DockMode", "WidgetsConfig", "ClockConfig", "DateConfig", "TimeConfig", "WeekdayConfig", "WeatherConfig", "WeatherLocation",
    "WeatherIcons", "TemperatureUnit", "WeatherSnapshot", "ElementConfig", "Spacing", "TextStyle", "BoldStyle", "DockSettings", "DockKeys",
]
