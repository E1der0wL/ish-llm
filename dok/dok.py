"""ish plugin entry point for a nonmodal floating shell dock."""

PLUGIN_META = {
    "name": "dok",
    "version": "0.1.0",
    "description": "Floating shell dock with configurable command-prefix shortcuts.",
    "author": "",
    "requirements": [],
    "dependencies": [],
}

from pathlib import Path

from .audio import AudioConfig, AudioSnapshot, AudioStatus
from .appearance import ProgressConfig, TitleConfig, TransportButton, TransportConfig, VisualizerConfig
from .config import ConfigStore, DockConfig, Width, WidthMode
from .controller import DockController
from .player import PlaybackAction, PlaybackSnapshot, PlaybackStatus
from .shortcuts import Separator, Shortcut, ShortcutLayout


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
    if controller.reload() and not store.path.exists():
        try:
            controller.save()
        except OSError as exc:
            logger = getattr(prompt, "logger", None)
            if logger is not None:
                logger.warning("dok default settings could not be saved: %s", exc)
    return controller


def uninstall(prompt) -> None:
    controller = getattr(prompt, DockController.INSTALLATION_ATTRIBUTE, None)
    if controller is not None:
        controller.uninstall()
