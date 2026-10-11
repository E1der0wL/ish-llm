"""Shared popup defaults and shortcut context for the page footer."""

from prompt_toolkit.widgets import Dialog as PTKDialog
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.keys import Keys

from ..ui.input.registry import ShortcutRegistry


class PopupInput:
    """Shared modal key receiver for popups whose body cannot take focus."""

    def __init__(self):
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.bindings.add(Keys.Any)(lambda event: None)
        self.receiver = FormattedTextControl("", focusable=True, show_cursor=False,
                                            key_bindings=self.shortcuts.bindings, modal=True)
        self.receiver.hub_shortcuts = self.shortcuts


class Dialog(PTKDialog):
    def __init__(self, *args, shortcut_context="dialog", **kwargs):
        self.shortcut_context = shortcut_context
        kwargs.setdefault("with_background", False)
        kwargs.setdefault("modal", False)
        super().__init__(*args, **kwargs)
