"""Shared popup defaults and shortcut context for the page footer."""

from prompt_toolkit.widgets import Dialog as PTKDialog
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.keys import Keys

from ..ui.input.registry import ShortcutRegistry
from .actions import bind_button_navigation


class DialogContext:
    DIALOG = "dialog"
    READER = "reader"


class PopupInput:
    """Shared modal key receiver for popups whose body cannot take focus."""

    EMPTY = ""
    FOCUSABLE = True
    SHOW_CURSOR = False
    MODAL = True
    NO_ACTION = None

    def __init__(self):
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.bindings.add(Keys.Any)(lambda event: self.NO_ACTION)
        self.receiver = FormattedTextControl(
            self.EMPTY,
            focusable=self.FOCUSABLE,
            show_cursor=self.SHOW_CURSOR,
            key_bindings=self.shortcuts.bindings,
            modal=self.MODAL,
        )
        self.shortcuts.attach(self.receiver)


class Dialog(PTKDialog):
    WITH_BACKGROUND = "with_background"
    MODAL = "modal"
    BUTTONS = "buttons"
    DEFAULT_BACKGROUND = False
    DEFAULT_MODAL = False

    def __init__(self, *args, shortcut_context=DialogContext.DIALOG, **kwargs):
        self.shortcut_context = shortcut_context
        kwargs.setdefault(self.WITH_BACKGROUND, self.DEFAULT_BACKGROUND)
        kwargs.setdefault(self.MODAL, self.DEFAULT_MODAL)
        super().__init__(*args, **kwargs)
        # Read-only help/log dialogs deliberately keep arrows as scroll keys.
        if shortcut_context != DialogContext.READER:
            bind_button_navigation(kwargs.get(self.BUTTONS) or [])
