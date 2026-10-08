"""Help, activity and other read-only results share one interaction contract."""

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.keys import Keys

from .dialog import Dialog
from .text_viewport import TextViewport
from ..ui.input.registry import ShortcutRegistry, MOVEMENT_KEYS


class ReadOnlyDialog(Dialog):
    def __init__(self, title, text, rows, columns, close, t, *, markdown=False, theme=None, output=None):
        height = lambda: Dimension.exact(max(1, min(30, rows() - 8)))
        self.output = output or TextViewport(text, markdown=markdown, theme=theme, tail=not markdown)
        self.output.window.height = height
        self.shortcuts = registry = ShortcutRegistry()
        registry.bindings.add(Keys.Any)(lambda event: None)
        for key, label in (("escape", "ESC"), ("c-l", "Ctrl+L"), ("enter", "Enter")):
            registry.add([key], label, "close", lambda e: close())
        def scroll(event):
            self.output.scroll(event.key_sequence[-1].key)
            event.app.invalidate()
        registry.add(MOVEMENT_KEYS, t("shortcut_movement"), "scroll", scroll)
        self.receiver = FormattedTextControl("", focusable=True, show_cursor=False,
                                            key_bindings=registry.bindings, modal=True)
        self.receiver.hub_shortcuts = registry
        super().__init__(title=title, shortcut_context="reader",
            body=HSplit([self.output, Window(self.receiver, height=0)]), buttons=[],
            width=lambda: Dimension(preferred=100, max=max(1, columns() - 4)))
