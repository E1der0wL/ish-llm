"""Input frame with a dedicated hint row inside the border."""

from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame


class InputFrame(Frame):
    def __init__(self, body, *, hint, **kwargs):
        super().__init__(HSplit([
            body,
            Window(FormattedTextControl(lambda: " " + hint() + " "),
                   height=1, wrap_lines=False, style="class:hub.muted"),
        ]), **kwargs)
