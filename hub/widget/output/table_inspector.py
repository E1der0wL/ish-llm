"""Keyboard-operated table inspection; all state is local to the popup."""

from hub.ui.output.worker import RenderSlot


from hub.locales.keys import MessageKey

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import (
    DynamicContainer,
    HSplit,
    UIContent,
    Window,
    ScrollOffsets,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.mouse_events import MouseEventType, MouseButton

from ..controls import Button, ScrollbarMargin
from ..clipboard import copy_text
from .selection import SelectableControl
from ..output_inspector import ExpandedOutputDialog
from ...ui.input.registry import MOVEMENT_KEYS
from ...ui.output.table import TableData
from ...ui.output.labels import LabelText
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...config.theme_style import ThemeSelector
from ..input_contract import MouseResult
from typing import NamedTuple


class TableCell:
    EMPTY = ""
    SPACE = " "
    MAX_WIDTH = 46


class PreparedTable(NamedTuple):
    data: TableData
    columns: tuple
    cells: tuple
    widths: tuple


def cell_text(value):
    return TableCell.EMPTY.join(
        char if char.isprintable() else TableCell.SPACE for char in value
    )


def display_cell(value, maximum=TableCell.MAX_WIDTH):
    """Bound painting cost, even when one cell contains a very long document."""
    result, used = [], 0
    for char in cell_text(value):
        size = get_cwidth(char)
        if used + size > maximum:
            return TableCell.EMPTY.join(result) + LabelText.ELLIPSIS
        result.append(char)
        used += size
    return TableCell.EMPTY.join(result)


class TableControl(SelectableControl):
    ABSENT = None
    EMPTY = ""
    ORIGIN = 0
    ASCENDING = False
    UNSTARTED = False
    STARTED = True
    MIN_HEIGHT = 1
    WRAP = False
    ARROWS = True
    SCROLL_OFFSET = 0
    HEADER_HEIGHT = 1
    MIN_COLUMN_WIDTH = 4
    HEADER_PADDING = 3
    CELL_PADDING = 2
    NO_ROW_WIDTH = 0
    PREVIOUS = -1
    NEXT = 1
    DESCENDING_MARK = " ↓"
    ASCENDING_MARK = " ↑"
    SHOW_CURSOR = False

    def __init__(self, item, context):
        super().__init__()
        self.item, self.context = item, context
        self.data = TableControl.ABSENT
        self.error = TableControl.EMPTY
        self.query = TableControl.EMPTY
        self.order = []
        self.row = self.column = self.left = TableControl.ORIGIN
        self.sort_column, self.descending = TableControl.ABSENT, TableControl.ASCENDING
        self.widths, self.cells, self.columns = (), (), ()
        self._started = TableControl.UNSTARTED
        self._height = TableControl.MIN_HEIGHT
        self.window = Window(
            self,
            wrap_lines=TableControl.WRAP,
            right_margins=[ScrollbarMargin(display_arrows=TableControl.ARROWS)],
            scroll_offsets=ScrollOffsets(
                top=TableControl.SCROLL_OFFSET, bottom=TableControl.SCROLL_OFFSET
            ),
            get_horizontal_scroll=lambda _: self.left,
        )
        self.header = Window(
            FormattedTextControl(
                self.header_text,
                get_cursor_position=lambda: Point(self.left, TableControl.ORIGIN),
            ),
            height=TableControl.HEADER_HEIGHT,
            get_horizontal_scroll=lambda _: self.left,
        )
        self.container = HSplit([self.header, self.window])

    def __pt_container__(self):
        return self.container

    def _load(self):
        self._started = TableControl.STARTED

        def build():
            data = TableData.from_block(self.item.block)
            columns = tuple(map(display_cell, data.columns))
            cells = tuple(tuple(map(display_cell, row)) for row in data.rows)
            widths = tuple(
                max(
                    TableControl.MIN_COLUMN_WIDTH,
                    get_cwidth(column) + TableControl.HEADER_PADDING,
                    max(
                        (
                            get_cwidth(row[i]) + TableControl.CELL_PADDING
                            for row in cells
                        ),
                        default=TableControl.NO_ROW_WIDTH,
                    ),
                )
                for i, column in enumerate(columns)
            )
            return PreparedTable(data, columns, cells, widths)

        def apply(result):
            if isinstance(result, str):
                self.error = result
            else:
                self.data, self.columns, self.cells, self.widths = result
                self.filter(self.query)
            get_app().invalidate()

        if self.context.render_worker and get_app().is_running:
            self.context.render_worker.request(
                RenderSlot.POPUP, build, apply, lambda error: str(error)
            )
        else:
            try:
                apply(build())
            except Exception as error:
                apply(str(error))

    def filter(self, query):
        self.selection.clear()
        self.query = query
        if self.data is TableControl.ABSENT:
            return
        selected = self.order[self.row] if self.order else TableControl.ABSENT
        self.order = self.data.select(query, self.sort_column, self.descending)
        self.row = (
            self.order.index(selected)
            if selected in self.order
            else TableControl.ORIGIN
        )

    def sort(self):
        self.descending = (
            not self.descending
            if self.sort_column == self.column
            else TableControl.ASCENDING
        )
        self.sort_column = self.column
        self.filter(self.query)

    def move(self, key):
        if self.data is TableControl.ABSENT:
            return
        if key in (KeyName.LEFT, KeyName.RIGHT):
            self.column = max(
                TableControl.ORIGIN,
                min(
                    len(self.columns) - 1,
                    self.column
                    + (
                        TableControl.PREVIOUS
                        if key == KeyName.LEFT
                        else TableControl.NEXT
                    ),
                ),
            )
            self.left = sum(self.widths[: self.column])
        elif key in (KeyName.HOME, KeyName.END):
            self.row = (
                TableControl.ORIGIN
                if key == KeyName.HOME
                else max(TableControl.ORIGIN, len(self.order) - 1)
            )
        else:
            delta = {
                KeyName.UP: TableControl.PREVIOUS,
                KeyName.DOWN: TableControl.NEXT,
                KeyName.PAGE_UP: -self._height,
                KeyName.PAGE_DOWN: self._height,
            }[key]
            self.row = max(
                TableControl.ORIGIN,
                min(max(TableControl.ORIGIN, len(self.order) - 1), self.row + delta),
            )

    def header_text(self):
        result = []
        for index, (value, width) in enumerate(zip(self.columns, self.widths)):
            marker = (
                (
                    TableControl.DESCENDING_MARK
                    if self.descending
                    else TableControl.ASCENDING_MARK
                )
                if index == self.sort_column
                else TableControl.EMPTY
            )
            text = value + marker

            def clicked(event, index=index):
                if (
                    event.event_type == MouseEventType.MOUSE_UP
                    and event.button == MouseButton.LEFT
                ):
                    self.column = index
                    self.sort()
                    get_app().invalidate()
                    return MouseResult.HANDLED
                return MouseResult.UNHANDLED

            result.append(
                (
                    (
                        ThemeSelector.SEARCH_CURRENT.style
                        if index == self.column
                        else ThemeSelector.ACCENT.style
                    ),
                    text
                    + TableCell.SPACE
                    * max(TableControl.ORIGIN, width - get_cwidth(text)),
                    clicked,
                )
            )
        return result

    def select_point(self, point):
        if not self.order:
            return
        self.row = min(len(self.order) - 1, point.y)
        cell = get_cwidth(self.selection.line(point.y)[: point.x])
        offset = TableControl.ORIGIN
        for index, width in enumerate(self.widths):
            offset += width
            if cell < offset:
                self.column = index
                break

    def create_content(self, width, height):
        if not self._started:
            self._load()
        self._height = max(TableControl.MIN_HEIGHT, height)

        def line(index):
            if not self.order:
                text = self.error or self.context.language(
                    MessageKey.OBJECT_EMPTY if self.data else MessageKey.OBJECT_LOADING
                )
                return [(ThemeSelector.MUTED.style, cell_text(text))]
            row = self.cells[self.order[index]]
            return [
                (
                    (
                        ThemeSelector.SEARCH_CURRENT.style
                        if index == self.row and i == self.column
                        else (
                            ThemeSelector.SEARCH_MATCH.style
                            if index == self.row
                            else TableControl.EMPTY
                        )
                    ),
                    value
                    + TableCell.SPACE
                    * max(TableControl.ORIGIN, cell_width - get_cwidth(value)),
                )
                for i, (value, cell_width) in enumerate(zip(row, self.widths))
            ]

        return self.select_content(
            UIContent(
                get_line=line,
                line_count=max(TableControl.MIN_HEIGHT, len(self.order)),
                cursor_position=Point(self.left, self.row),
                show_cursor=TableControl.SHOW_CURSOR,
            ),
            width,
        )

    def retry_unloaded(self):
        if self.data is TableControl.ABSENT and not self.error:
            self._started = TableControl.UNSTARTED

    def selected_value(self):
        if self.data and self.order:
            return self.data.rows[self.order[self.row]][self.column]
        return TableControl.ABSENT


class TableInspector(ExpandedOutputDialog):
    TABLE_MODE = False
    RAW_MODE = True
    NEXT_MATCH = 1
    PREVIOUS_MATCH = -1
    ACTION_POSITION = 1
    ABSENT = None
    EMPTY = ""
    COUNT_ORIGIN = 0
    DISPLAY_OFFSET = 1
    SEPARATOR = " • "
    NOTICE_FORMAT = " • {notice}"

    def __init__(self, item, context):
        self.table = TableControl(item, context)
        self.raw_mode = TableInspector.TABLE_MODE
        output = context.viewport(item)
        output.raw_mode = TableInspector.RAW_MODE
        super().__init__(
            item,
            context,
            output,
            body=DynamicContainer(lambda: output if self.raw_mode else self.table),
        )
        self.shortcuts.add(
            MOVEMENT_KEYS,
            self.t(MessageKey.SHORTCUT_MOVEMENT),
            ShortcutAction.SELECT,
            lambda e: self.move(e.key_sequence[-1].key),
        )
        self.shortcuts.add(
            [KeyName.ENTER, KeyName.S],
            ShortcutLabel.ENTER_S,
            ShortcutAction.OBJECT_SORT,
            lambda e: self.table.sort(),
            when=lambda: not self.raw_mode,
        )
        self.shortcuts.add(
            [KeyName.R],
            KeyName.R,
            ShortcutAction.OBJECT_RAW,
            lambda e: self.toggle_raw(),
        )
        self.shortcuts.add(
            [KeyName.N, KeyName.CONTROL_N],
            ShortcutLabel.N_CONTROL_N,
            ShortcutAction.NEXT_MATCH,
            lambda e: output.move_match(TableInspector.NEXT_MATCH),
            when=lambda: self.raw_mode,
        )
        self.shortcuts.add(
            [KeyName.N_UPPER, KeyName.CONTROL_P],
            ShortcutLabel.N_CONTROL_P,
            ShortcutAction.PREVIOUS_MATCH,
            lambda e: output.move_match(TableInspector.PREVIOUS_MATCH),
            when=lambda: self.raw_mode,
        )
        self.buttons[
            TableInspector.ACTION_POSITION : TableInspector.ACTION_POSITION
        ] = [
            Button(self.t(MessageKey.SHORTCUT_OBJECT_SORT), handler=self.table.sort),
            Button(self.t(MessageKey.OBJECT_RAW), handler=self.toggle_raw),
            Button(
                self.t(MessageKey.OBJECT_COPY_CELL),
                handler=lambda: get_app().create_background_task(self.copy_cell()),
            ),
        ]

    async def copy_cell(self):
        value = self.table.selected_value()
        if value is not TableControl.ABSENT:
            await copy_text(value)
            get_app().invalidate()

    def move(self, key):
        self.output.scroll(key) if self.raw_mode else self.table.move(key)

    def changed_query(self, query):
        self.table.filter(query)
        super().changed_query(query)

    def toggle_raw(self):
        self.table.selection.clear()
        self.output.selection.clear()
        self.raw_mode = not self.raw_mode
        # Both surfaces share the popup worker slot. Re-request any surface
        # whose queued render was superseded while it was hidden.
        if self.context.render_worker:
            self.context.render_worker.cancel(RenderSlot.POPUP)
        self.output.reset_render_request()
        self.table.retry_unloaded()

    def status(self):
        if self.raw_mode:
            detail = self.t(
                MessageKey.SEARCH_COUNT,
                index=self.output.hit_index + TableInspector.DISPLAY_OFFSET,
                count=len(self.output.hits),
            )
            return self.t(MessageKey.OBJECT_RAW) + TableInspector.SEPARATOR + detail
        table = self.table
        detail = self.t(
            MessageKey.OBJECT_TABLE_COUNT,
            index=(
                table.row + TableInspector.DISPLAY_OFFSET
                if table.order
                else TableInspector.COUNT_ORIGIN
            ),
            visible=len(table.order),
            total=len(table.data.rows) if table.data else TableInspector.COUNT_ORIGIN,
            column=table.column + TableInspector.DISPLAY_OFFSET,
        )
        return detail + (
            TableInspector.NOTICE_FORMAT.format(notice=self.notice)
            if self.notice
            else TableInspector.EMPTY
        )

    def selected_text(self):
        return (self.output if self.raw_mode else self.table).selection.text
