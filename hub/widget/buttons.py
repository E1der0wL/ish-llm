"""Buttons with draft-state labels independent of settings controllers."""

from .controls import Button


class SaveButton(Button):
    DEFAULT_WIDTH = 10
    DIRTY_PREFIX = "* "
    CLEAN_PREFIX = ""

    def __init__(self, label, dirty, handler, *, width=DEFAULT_WIDTH):
        self.label, self.dirty = label, dirty
        super().__init__(
            lambda: (self.DIRTY_PREFIX if self.dirty() else self.CLEAN_PREFIX)
            + self.label(),
            handler=handler,
            width=width,
        )
