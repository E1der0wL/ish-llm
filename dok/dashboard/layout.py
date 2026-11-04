"""Calculate dock height and fallback modes from configured widget rows."""
from __future__ import annotations

from ..core.state import DockMode
from ..features.shortcuts.config import Shortcut
from ..settings.config import DockConfig
from .widgets import body_widgets


class DockGeometry:
    @classmethod
    def body_rows(cls, mode, config=None):
        config = config if config is not None else DockConfig()
        return tuple(row for widget in body_widgets(config) for row in widget.rows(mode))

    @classmethod
    def height(cls, mode, extra_rows=0, config=None):
        return len(cls.body_rows(mode, config)) + extra_rows

    @classmethod
    def fits(cls, available_height, body_width, mode=DockMode.BASIC, extra_rows=0, config=None):
        height = cls.height(mode, extra_rows, config)
        return height > 0 and available_height >= height + 1 and body_width >= Shortcut.minimum_width()

    @classmethod
    def resolve(cls, requested, available_height, body_width, extra_rows, config=None):
        if requested == DockMode.HIDDEN:
            return DockMode.HIDDEN
        if requested == DockMode.MAXIMIZED and cls.fits(available_height, body_width, requested, extra_rows, config):
            return DockMode.MAXIMIZED
        return DockMode.BASIC if cls.fits(available_height, body_width, config=config) else DockMode.HIDDEN
