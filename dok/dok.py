"""ish plugin entry point for a nonmodal floating shell dock."""

PLUGIN_META = {
    "name": "dok",
    "version": "0.1.0",
    "description": "Floating shell dock with configurable command-prefix shortcuts.",
    "author": "",
    "requirements": [],
    "dependencies": ["pyfiglet", "httpx"],
}

from pathlib import Path

from .app.controller import DockController
from .core.elements import ElementConfig
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
from .settings.config import DockConfig, DockSettings, DockKeys, Width, WidthMode
from .settings.store import ConfigStore


def install(prompt, *, config_path: str | Path | None = None) -> DockController:
    """Install/reload from .ishrc.py; failures preserve a prior working configuration."""
    if config_path is None:
        from ish.config import config
        store = ConfigStore.for_home(config.ISH_HOME)
    else:
        store = ConfigStore(config_path)
    existing = getattr(prompt, DockController.INSTALLATION_ATTRIBUTE, None)
    if existing is not None:
        existing.reload(store)
        return existing
    controller = DockController(prompt, store)
    controller.attach()
    setattr(prompt, DockController.INSTALLATION_ATTRIBUTE, controller)
    controller.reload()
    return controller


def uninstall(prompt) -> None:
    controller = getattr(prompt, DockController.INSTALLATION_ATTRIBUTE, None)
    if controller is not None:
        controller.uninstall()
