"""Session creation and clone-boundary form, independent of backend loading."""

from hub.locales.keys import MessageKey
from hub.backend.session_contracts import SessionMode
from hub.backend.history_values import HistoryRow

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, ConditionalContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ..controls import RadioList, TextArea


class SessionForm:
    SELECT_ON_FOCUS = True
    NAME_HEIGHT = 1
    NAME_MULTILINE = False
    BOUNDARY_MIN_ROWS = 1
    BOUNDARY_MAX_ROWS = 6
    PADDING = 1
    LATEST_BOUNDARY = None

    def __init__(self, t, *, mode, title, allow_clone, turns, boundary, turn_label):
        self.t, self.turn_label = t, turn_label
        self.mode = RadioList(
            [
                (SessionMode.NEW.value, t(MessageKey.CREATE)),
                *(
                    [(SessionMode.CLONE.value, t(MessageKey.CLONE))]
                    if allow_clone
                    else []
                ),
            ],
            default=mode,
            select_on_focus=self.SELECT_ON_FOCUS,
        )
        self.name = TextArea(
            text=title, height=self.NAME_HEIGHT, multiline=self.NAME_MULTILINE
        )
        self.boundary = RadioList(
            self.options(turns), default=boundary, select_on_focus=self.SELECT_ON_FOCUS
        )
        self.boundary.window.height = Dimension(
            min=self.BOUNDARY_MIN_ROWS, max=self.BOUNDARY_MAX_ROWS
        )
        self.container = HSplit(
            [
                self.mode,
                ConditionalContainer(
                    HSplit([Label(t(MessageKey.CLONE_BOUNDARY)), self.boundary]),
                    Condition(lambda: self.mode.current_value == SessionMode.CLONE),
                ),
                Label(t(MessageKey.NAME)),
                self.name,
                Label(t(MessageKey.AUTO_NAME_HINT)),
            ],
            padding=self.PADDING,
        )

    def options(self, turns):
        return [
            (self.LATEST_BOUNDARY, self.t(MessageKey.CLONE_LATEST)),
            *[
                (HistoryRow(row).id, self.turn_label(row, i, self.t))
                for i, row in enumerate(turns or [])
            ],
        ]

    def set_turns(self, rows, selected):
        self.boundary.values = self.options(rows)
        self.boundary.current_value = (
            selected
            if any(HistoryRow(row).id == selected for row in rows)
            else self.LATEST_BOUNDARY
        )
        self.boundary._selected_index = next(
            i
            for i, (value, _) in enumerate(self.boundary.values)
            if value == self.boundary.current_value
        )

    def __pt_container__(self):
        return self.container
