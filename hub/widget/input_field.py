"""An editable field with an adjacent action, usable in forms and dialogs."""

from prompt_toolkit.layout import VSplit

from .controls import Button, TextArea


class InputField(VSplit):
    def __init__(self, *, input=None, text="", button_text="…", on_action=None, button_width=8):
        self.input = input if input is not None else TextArea(text=text, multiline=False, height=1)
        self.button = Button(button_text, handler=on_action, width=button_width)
        super().__init__([self.input, self.button], padding=1)

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
