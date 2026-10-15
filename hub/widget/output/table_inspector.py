"""Keyboard-operated table inspection; all state is local to the popup."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import DynamicContainer, HSplit, UIContent, Window, ScrollOffsets
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.mouse_events import MouseEventType, MouseButton

from ..controls import Button, ScrollbarMargin
from ..clipboard import copy_text
from .selection import SelectableControl
from ..output_inspector import ExpandedOutputDialog
from ...ui.input.registry import MOVEMENT_KEYS
from ...ui.output.table import TableData


def cell_text(value):
    return "".join(char if char.isprintable() else " " for char in value)


def display_cell(value, maximum=46):
    """Bound painting cost, even when one cell contains a very long document."""
    result, used = [], 0
    for char in cell_text(value):
        size = get_cwidth(char)
        if used + size > maximum:
            return "".join(result) + "…"
        result.append(char)
        used += size
    return "".join(result)


class TableControl(SelectableControl):
    def __init__(self, item, context):
        super().__init__()
        self.item, self.context = item, context
        self.data = None
        self.error = ""
        self.query = ""
        self.order = []
        self.row = self.column = self.left = 0
        self.sort_column, self.descending = None, False
        self.widths, self.cells, self.columns = (), (), ()
        self._started = False
        self._height = 1
        self.window = Window(self, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)],
                             scroll_offsets=ScrollOffsets(top=0, bottom=0),
                             get_horizontal_scroll=lambda _: self.left)
        self.header = Window(FormattedTextControl(self.header_text,
                             get_cursor_position=lambda: Point(self.left, 0)), height=1,
                             get_horizontal_scroll=lambda _: self.left)
        self.container = HSplit([self.header, self.window])

    def __pt_container__(self):
        return self.container

    def _load(self):
        self._started = True
        def build():
            data = TableData.from_block(self.item.block)
            columns = tuple(map(display_cell, data.columns))
            cells = tuple(tuple(map(display_cell, row)) for row in data.rows)
            widths = tuple(max(4, get_cwidth(column) + 3,
                               max((get_cwidth(row[i]) + 2 for row in cells), default=0))
                           for i, column in enumerate(columns))
            return data, columns, cells, widths
        def apply(result):
            if isinstance(result, str):
                self.error = result
            else:
                self.data, self.columns, self.cells, self.widths = result
                self.filter(self.query)
            get_app().invalidate()
        if self.context.render_worker and get_app().is_running:
            self.context.render_worker.request("popup", build, apply, lambda error: str(error))
        else:
            try:
                apply(build())
            except Exception as error:
                apply(str(error))

    def filter(self, query):
        self.selection.clear()
        self.query = query
        if self.data is None:
            return
        selected = self.order[self.row] if self.order else None
        self.order = self.data.select(query, self.sort_column, self.descending)
        self.row = self.order.index(selected) if selected in self.order else 0

    def sort(self):
        self.descending = not self.descending if self.sort_column == self.column else False
        self.sort_column = self.column
        self.filter(self.query)

    def move(self, key):
        if self.data is None:
            return
        if key in ("left", "right"):
            self.column = max(0, min(len(self.columns) - 1, self.column + (-1 if key == "left" else 1)))
            self.left = sum(self.widths[:self.column])
        elif key in ("home", "end"):
            self.row = 0 if key == "home" else max(0, len(self.order) - 1)
        else:
            delta = {"up": -1, "down": 1, "pageup": -self._height, "pagedown": self._height}[key]
            self.row = max(0, min(max(0, len(self.order) - 1), self.row + delta))

    def header_text(self):
        result = []
        for index, (value, width) in enumerate(zip(self.columns, self.widths)):
            marker = (" ↓" if self.descending else " ↑") if index == self.sort_column else ""
            text = value + marker
            def clicked(event, index=index):
                if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                    self.column = index
                    self.sort()
                    get_app().invalidate()
                    return None
                return NotImplemented
            result.append(("class:hub.search.current" if index == self.column else "class:hub.accent",
                           text + " " * max(0, width - get_cwidth(text)), clicked))
        return result

    def select_point(self, point):
        if not self.order:
            return
        self.row = min(len(self.order) - 1, point.y)
        cell = get_cwidth(self.selection.line(point.y)[:point.x])
        offset = 0
        for index, width in enumerate(self.widths):
            offset += width
            if cell < offset:
                self.column = index
                break

    def create_content(self, width, height):
        if not self._started:
            self._load()
        self._height = max(1, height)
        def line(index):
            if not self.order:
                text = self.error or self.context.language("object_empty" if self.data else "object_loading")
                return [("class:hub.muted", cell_text(text))]
            row = self.cells[self.order[index]]
            return [("class:hub.search.current" if index == self.row and i == self.column else
                     "class:hub.search.match" if index == self.row else "",
                     value + " " * max(0, cell_width - get_cwidth(value)))
                    for i, (value, cell_width) in enumerate(zip(row, self.widths))]
        return self.select_content(UIContent(get_line=line, line_count=max(1, len(self.order)),
                         cursor_position=Point(self.left, self.row), show_cursor=False), width)


class TableInspector(ExpandedOutputDialog):
    def __init__(self, item, context):
        self.table = TableControl(item, context)
        self.raw_mode = False
        output = context.viewport(item)
        output.raw_mode = True
        super().__init__(item, context, output,
                         body=DynamicContainer(lambda: output if self.raw_mode else self.table))
        self.shortcuts.add(MOVEMENT_KEYS, self.t("shortcut_movement"), "select",
                           lambda e: self.move(e.key_sequence[-1].key))
        self.shortcuts.add(["enter", "s"], "Enter/s", "object_sort", lambda e: self.table.sort(),
                           when=lambda: not self.raw_mode)
        self.shortcuts.add(["r"], "r", "object_raw", lambda e: self.toggle_raw())
        self.shortcuts.add(["n", "c-n"], "n/Ctrl+N", "next_match", lambda e: output.move_match(1),
                           when=lambda: self.raw_mode)
        self.shortcuts.add(["N", "c-p"], "N/Ctrl+P", "previous_match", lambda e: output.move_match(-1),
                           when=lambda: self.raw_mode)
        self.buttons[1:1] = [
            Button(self.t("shortcut_object_sort"), handler=self.table.sort),
            Button(self.t("object_raw"), handler=self.toggle_raw),
            Button(self.t("object_copy_cell"), handler=lambda: get_app().create_background_task(self.copy_cell())),
        ]

    async def copy_cell(self):
        if self.table.data and self.table.order:
            value = self.table.data.rows[self.table.order[self.table.row]][self.table.column]
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
            self.context.render_worker.cancel("popup")
        if self.output._resize_timer:
            self.output._resize_timer.cancel()
            self.output._resize_timer = None
        self.output._requested_key = None
        if self.table.data is None and not self.table.error:
            self.table._started = False

    def status(self):
        if self.raw_mode:
            detail = self.t("search_count", index=self.output.hit_index + 1, count=len(self.output.hits))
            return self.t("object_raw") + " · " + detail
        table = self.table
        detail = self.t("object_table_count", index=table.row + 1 if table.order else 0,
                        visible=len(table.order), total=len(table.data.rows) if table.data else 0,
                        column=table.column + 1)
        return detail + (f" · {self.notice}" if self.notice else "")

    def selected_text(self):
        return (self.output if self.raw_mode else self.table).selection.text
