"""Input frame with a dedicated hint row inside the border."""

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame
from ..config.theme_style import ThemeSelector


class InputFrame(Frame):
    NO_ACTIONS = None
    HINT_PADDING = " "
    HINT_ROWS = 1
    WRAP_HINT = False

    def __init__(self, body, *, hint, actions=NO_ACTIONS, **kwargs):
        hint_row = Window(
            FormattedTextControl(
                lambda: self.HINT_PADDING + hint() + self.HINT_PADDING
            ),
            height=self.HINT_ROWS,
            wrap_lines=self.WRAP_HINT,
            style=ThemeSelector.MUTED.style,
        )
        super().__init__(
            HSplit(
                [
                    body,
                    (
                        VSplit([hint_row, actions], height=self.HINT_ROWS)
                        if actions is not self.NO_ACTIONS
                        else hint_row
                    ),
                ]
            ),
            **kwargs
        )
