"""Input frame with a dedicated hint row inside the border."""

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame


class InputFrame(Frame):
    def __init__(self, body, *, hint, actions=None, **kwargs):
        hint_row = Window(FormattedTextControl(lambda: " " + hint() + " "),
                          height=1, wrap_lines=False, style="class:hub.muted")
        super().__init__(HSplit([
            body,
            VSplit([hint_row, actions], height=1) if actions is not None else hint_row,
        ]), **kwargs)
