"""Shared popup defaults and shortcut context for the page footer."""

from prompt_toolkit.widgets import Dialog as PTKDialog


class Dialog(PTKDialog):
    def __init__(self, *args, shortcut_context="dialog", **kwargs):
        self.shortcut_context = shortcut_context
        kwargs.setdefault("with_background", False)
        kwargs.setdefault("modal", False)
        super().__init__(*args, **kwargs)
