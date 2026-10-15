"""Buttons with draft-state labels independent of settings controllers."""

from .controls import Button


class SaveButton(Button):
    def __init__(self, label, dirty, handler, *, width=10):
        self.label, self.dirty = label, dirty
        super().__init__(lambda: ("* " if self.dirty() else "") + self.label(), handler=handler, width=width)
