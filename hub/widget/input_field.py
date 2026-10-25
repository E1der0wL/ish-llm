"""An editable field with an adjacent action, usable in forms and dialogs."""

from prompt_toolkit.layout import VSplit

from .controls import Button, TextArea


class InputField(VSplit):
    ABSENT = None
    EMPTY_TEXT = ""
    ACTION_LABEL = "…"
    ACTION_WIDTH = 8
    MULTILINE = False
    ROWS = 1
    GAP = 1

    def __init__(
        self,
        *,
        input=ABSENT,
        text=EMPTY_TEXT,
        button_text=ACTION_LABEL,
        on_action=ABSENT,
        button_width=ACTION_WIDTH
    ):
        self.input = (
            input
            if input is not self.ABSENT
            else TextArea(text=text, multiline=self.MULTILINE, height=self.ROWS)
        )
        self.button = Button(button_text, handler=on_action, width=button_width)
        super().__init__([self.input, self.button], padding=self.GAP)

    @property
    def buffer(self):
        return self.input.buffer

    @property
    def control(self):
        return self.input.control

    @property
    def text(self):
        return self.input.text

    @text.setter
    def text(self, value):
        self.input.text = value
