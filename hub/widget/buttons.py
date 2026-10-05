"""Buttons with draft-state labels independent of settings controllers."""

from .controls import Button


class SaveButton(Button):
    def __init__(self, label, dirty, handler, *, width=10):
        self.label, self.dirty = label, dirty
        super().__init__(label(), handler=handler, width=width)

    def _get_text_fragments(self):
        self.text = ("* " if self.dirty() else "") + self.label()
        return super()._get_text_fragments()
