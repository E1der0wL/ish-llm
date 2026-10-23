"""Help, activity and other read-only results share one interaction contract."""

from hub.locales.keys import MessageKey

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.dimension import Dimension

from ..dialog import Dialog, PopupInput
from ..controls import Button
from prompt_toolkit.key_binding import merge_key_bindings
from .text_viewport import TextViewport
from ...ui.input.registry import MOVEMENT_KEYS


class ReadOnlyDialog(Dialog):
    def __init__(self, title, text, rows, columns, close, t, *, markdown=False, theme=None, render_worker=None):
        self.output = TextViewport(text, markdown=markdown, theme=theme, tail=not markdown, render_worker=render_worker)
        self.output.window.height = lambda: Dimension.exact(max(1, min(30, rows() - 8)))
        navigation = PopupInput()
        self.shortcuts = registry = navigation.shortcuts
        self.receiver = navigation.receiver
        for key, label in (("escape", "ESC"), ("c-l", "Ctrl+L"), ("enter", "Enter")):
            registry.add([key], label, "close", lambda e: close())
        def scroll(event):
            self.output.scroll(event.key_sequence[-1].key)
            event.app.invalidate()
        registry.add(MOVEMENT_KEYS, t(MessageKey.SHORTCUT_MOVEMENT), "scroll", scroll)
        self.close_button = Button(t(MessageKey.HISTORY_CLOSE), handler=close)
        self.close_button.control.key_bindings = merge_key_bindings([
            self.close_button.control.key_bindings, registry.bindings])
        self.close_button.control.hub_shortcuts = registry
        super().__init__(title=title, shortcut_context="reader",
            body=HSplit([self.output, Window(self.receiver, height=0)]), buttons=[self.close_button],
            width=lambda: Dimension(preferred=100, max=max(1, columns() - 4)))
