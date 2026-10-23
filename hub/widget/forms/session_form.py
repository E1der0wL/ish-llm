"""Session creation and clone-boundary form, independent of backend loading."""

from hub.locales.keys import MessageKey

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, ConditionalContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ..controls import RadioList, TextArea


class SessionForm:
    def __init__(self, t, *, mode, title, allow_clone, turns, boundary, turn_label):
        self.t, self.turn_label = t, turn_label
        self.mode = RadioList([("new", t(MessageKey.CREATE)), *([("clone", t(MessageKey.CLONE))] if allow_clone else [])],
                              default=mode, select_on_focus=True)
        self.name = TextArea(text=title, height=1, multiline=False)
        self.boundary = RadioList(self.options(turns), default=boundary, select_on_focus=True)
        self.boundary.window.height = Dimension(min=1, max=6)
        self.container = HSplit([
            self.mode, ConditionalContainer(HSplit([Label(t(MessageKey.CLONE_BOUNDARY)), self.boundary]),
                Condition(lambda: self.mode.current_value == "clone")),
            Label(t(MessageKey.NAME)), self.name, Label(t(MessageKey.AUTO_NAME_HINT)),
        ], padding=1)

    def options(self, turns):
        return [(None, self.t(MessageKey.CLONE_LATEST)),
                *[(row["id"], self.turn_label(row, i, self.t)) for i, row in enumerate(turns or [])]]

    def set_turns(self, rows, selected):
        self.boundary.values = self.options(rows)
        self.boundary.current_value = selected if any(row["id"] == selected for row in rows) else None
        self.boundary._selected_index = next(i for i, (value, _) in enumerate(self.boundary.values)
                                             if value == self.boundary.current_value)

    def __pt_container__(self):
        return self.container
