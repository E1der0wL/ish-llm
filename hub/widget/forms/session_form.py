"""Separate name-only creation and conversation-boundary cloning forms."""

from hub.locales.keys import MessageKey
from hub.backend.history_values import HistoryRow

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension

from ..controls import RadioList, TextArea
from ..dialog import PopupGeometry, PopupSize
from ..section import Heading
from ..summary import clipped_summary, turn_label


class SessionNameForm:
    NAME_HEIGHT = 1

    def __init__(self, t, title):
        self.name = TextArea(text=title, height=self.NAME_HEIGHT, multiline=False)
        self.container = HSplit([Heading(t(MessageKey.NAME)), self.name])

    def __pt_container__(self):
        return self.container


class SessionCloneForm(SessionNameForm):
    BOUNDARY_ROWS = 6
    PREVIEW_ROWS = 8
    LABEL_MARGIN = 10
    LATEST_BOUNDARY = None
    PARAGRAPH = "\n\n"

    def __init__(self, t, title, *, turns, boundary):
        super().__init__(t, title)
        self.t = t
        self.rows = list(turns or [])
        self.preview = TextArea(
            read_only=True, scrollbar=True, wrap_lines=True,
            height=Dimension(min=1, preferred=self.PREVIEW_ROWS),
        )
        self.boundary = RadioList(
            self.options(), default=boundary, select_on_focus=True,
            on_select=self.select,
        )
        self.boundary.window.height = Dimension(min=1, max=self.BOUNDARY_ROWS)
        self.container = HSplit([
            Heading(t(MessageKey.NAME)), self.name,
            Heading(t(MessageKey.CLONE_BOUNDARY)), self.boundary,
            Heading(t(MessageKey.SESSION_CLONE_PREVIEW)), self.preview,
        ])
        self.select(self.boundary.current_value)

    def options(self):
        return [
            (self.LATEST_BOUNDARY, self.t(MessageKey.CLONE_LATEST)),
            *[(HistoryRow(row).id, lambda row=row, i=i: clipped_summary(
                turn_label(row, i, self.t),
                PopupGeometry.dimensions(PopupSize.MEDIUM)[0].preferred
                - self.LABEL_MARGIN,
            )) for i, row in enumerate(self.rows)],
        ]

    def select(self, identifier):
        row = next((row for row in self.rows if HistoryRow(row).id == identifier), None)
        if identifier is self.LATEST_BOUNDARY and self.rows:
            row = self.rows[-1]
        self.preview.text = self.PARAGRAPH.join((
            self.t(MessageKey.HISTORY_USER_MESSAGE), HistoryRow(row).text,
            self.t(MessageKey.HISTORY_RESPONSE), HistoryRow(row).response,
        )) if row is not None else ""

    def set_turns(self, rows, selected):
        self.rows = list(rows)
        self.boundary.values = self.options()
        self.boundary.current_value = (
            selected if any(HistoryRow(row).id == selected for row in rows)
            else self.LATEST_BOUNDARY
        )
        self.boundary._selected_index = next(
            i for i, (value, _) in enumerate(self.boundary.values)
            if value == self.boundary.current_value
        )
        self.select(self.boundary.current_value)
