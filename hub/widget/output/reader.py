"""Help, activity and other read-only results share one interaction contract."""

from hub.ui.input.registry import ShortcutRegistry


from hub.locales.keys import MessageKey

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.dimension import Dimension

from ..dialog import Dialog, PopupInput, DialogContext
from ..controls import Button
from prompt_toolkit.key_binding import merge_key_bindings
from .text_viewport import TextViewport
from ...ui.input.registry import MOVEMENT_KEYS
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction
from ...config.theme_style import ThemeSelector


class ReadOnlyDialog(Dialog):
    MARKDOWN = False
    ABSENT = None
    MIN_ROWS = 1
    MAX_ROWS = 30
    RECEIVER_HEIGHT = 0
    CLOSE_KEYS = (
        (KeyName.ESCAPE, ShortcutLabel.ESCAPE),
        (KeyName.CONTROL_L, ShortcutLabel.CONTROL_L),
        (KeyName.ENTER, ShortcutLabel.ENTER),
    )

    def __init__(
        self,
        title,
        text,
        rows,
        columns,
        close,
        t,
        *,
        markdown=MARKDOWN,
        theme=ABSENT,
        render_worker=ABSENT
    ):
        self.output = TextViewport(
            text,
            markdown=markdown,
            theme=theme,
            tail=not markdown,
            render_worker=render_worker,
        )
        self.output.window.height = Dimension(
            min=self.MIN_ROWS, preferred=self.MAX_ROWS, max=self.MAX_ROWS
        )
        self.output.window.style = ThemeSelector.POPUP_OUTPUT.style
        navigation = PopupInput()
        self.shortcuts = registry = navigation.shortcuts
        self.receiver = navigation.receiver
        for key, label in self.CLOSE_KEYS:
            registry.add([key], label, ShortcutAction.CLOSE, lambda e: close())

        def scroll(event):
            self.output.scroll(event.key_sequence[-1].key)
            event.app.invalidate()

        registry.add(
            MOVEMENT_KEYS,
            t(MessageKey.SHORTCUT_MOVEMENT),
            ShortcutAction.SCROLL,
            scroll,
        )
        self.close_button = Button(t(MessageKey.HISTORY_CLOSE), handler=close)
        self.close_button.control.key_bindings = merge_key_bindings(
            [self.close_button.control.key_bindings, registry.bindings]
        )
        registry.attach(self.close_button.control)
        super().__init__(
            title=title,
            shortcut_context=DialogContext.READER,
            body=HSplit(
                [self.output, Window(self.receiver, height=self.RECEIVER_HEIGHT)]
            ),
            buttons=[self.close_button],
        )
