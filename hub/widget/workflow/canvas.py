"""Cell-based graph canvas with viewport rendering and keyboard/mouse parity."""

from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowEdge
from llm.components.workflows.contracts import WorkflowNode

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import UIContent, UIControl
from prompt_toolkit.mouse_events import MouseButton, MouseEventType, MouseEvent
from prompt_toolkit.utils import get_cwidth

from ...config.theme_style import ThemeSelector, ThemeStyle
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...locales.keys import MessagePattern
from ..mouse_contract import MouseBinding
from ..mouse import ScrollbarGeometry
from .draft import NodePosition


class CanvasWindow(Window):
    """Use Hub mouse capture, including releases outside the canvas."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.origin = Point(0, 0)
        super().__init__(canvas, wrap_lines=False, always_hide_cursor=True,
                         style=ThemeSelector.WORKFLOW_CANVAS.style)
        MouseBinding.set_scroll(self, canvas)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        self.origin = Point(write_position.xpos, write_position.ypos)
        return super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)

    def drag_handler(self, point):
        origin = self.origin
        def event(position, kind):
            return MouseEvent(Point(position.x - origin.x, position.y - origin.y),
                              kind, MouseButton.LEFT, frozenset())
        self.canvas.mouse_handler(event(point, MouseEventType.MOUSE_DOWN))
        def drag(position, finished=False):
            self.canvas.mouse_handler(event(position, MouseEventType.MOUSE_UP if finished else MouseEventType.MOUSE_MOVE))
        return drag


class GraphCanvas(UIControl):
    NODE_WIDTH = 25
    NODE_HEIGHT = 5
    PAN_ROWS = 3
    DIRECTIONS = {KeyName.LEFT: (-1, 0), KeyName.RIGHT: (1, 0), KeyName.UP: (0, -1), KeyName.DOWN: (0, 1)}
    KEY_LABELS = {KeyName.CONTROL_Z: 'Ctrl+Z', KeyName.CONTROL_Y: 'Ctrl+Y'}
    SELECT_KEYS = '←↓↑→'
    SCROLL_KEYS = 'Alt+←↓↑→/PgUp/PgDn/Home/End'
    HELP_LINE = '{label}  •  {description}'
    NEWLINE = '\n'
    CARRIAGE_RETURN = '\r'
    SPACE = ' '
    HORIZONTAL = '─'
    VERTICAL = '│'
    TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT = '┌', '┐', '└', '┘'
    ARROW = '▶'
    ENTRY_MARKER, PLAIN_MARKER = '▶ ', '  '
    UNKNOWN_KIND = '?'
    ELLIPSIS = '…'
    PAN_MARGIN = 2
    MIN_REVEAL_WIDTH, MIN_REVEAL_HEIGHT = 5, 2
    VERTICAL_DISTANCE_WEIGHT = 3
    EDGE_ROW = 2
    PORT_CHARS = 14
    NODE_BORDER_COLUMNS = 2
    TITLE_RESERVED_COLUMNS = 6
    CONTENT_RESERVED_COLUMNS = 5
    CONTENT_COLUMN = 2
    TYPE_ROW, ID_ROW = 2, 3

    def __init__(self, draft, selected, select, move_node, actions):
        self.draft, self.selected, self.select = draft, selected, select
        self.move_node = move_node
        self.offset_x = self.offset_y = 0
        self.width = self.height = 1
        self.drag = None
        self.drag_position = None
        self.pan_anchor = None
        self.errors = frozenset()
        self._cache_key, self._lines = None, []
        self.registry = ShortcutRegistry().attach(self)
        self.bindings = self.registry.bindings
        self._help = []
        self.bind(list(self.DIRECTIONS), self.SELECT_KEYS, MessagePattern.shortcut(ShortcutAction.SELECT),
                  lambda e: self.navigate(*self.DIRECTIONS[e.key_sequence[-1].key]))
        self.registry.scroll(lambda e: self.scroll(e.key_sequence[-1].key))
        self._help.append((self.SCROLL_KEYS, MessagePattern.shortcut(ShortcutAction.SCROLL)))
        for key, (action, description) in actions.items():
            label = self.KEY_LABELS.get(key, key)
            self.bind([key], label, description, lambda e, callback=action: callback())
        MouseBinding.keep_selection_on_empty(self)
        self.window = CanvasWindow(self)

    def bind(self, keys, label, description, callback):
        self.registry.add(keys, ShortcutLabel.HIDDEN, ShortcutAction.NONE, callback)
        self._help.append((label, description))

    def help_text(self, translate):
        return self.NEWLINE.join(self.HELP_LINE.format(label=label, description=translate(description))
                                 for label, description in self._help)

    def is_focusable(self):
        return True

    def get_key_bindings(self):
        return self.bindings

    def __pt_container__(self):
        return self.window

    def positions(self):
        positions = self.draft.positions()
        if self.drag_position is not None and self.drag is not None:
            positions[self.drag[0]] = self.drag_position
        return positions

    def hit(self, x, y):
        x, y = x + self.offset_x, y + self.offset_y
        for name, pos in reversed(list(self.positions().items())):
            if pos.x <= x < pos.x + self.NODE_WIDTH and pos.y <= y < pos.y + self.NODE_HEIGHT:
                return name
        return None

    def pan(self, dx, dy):
        positions = self.positions().values()
        max_x = max((p.x + self.NODE_WIDTH for p in positions), default=self.width)
        max_y = max((p.y + self.NODE_HEIGHT for p in self.positions().values()), default=self.height)
        self.offset_x = min(max(0, max_x - self.width + self.PAN_MARGIN), max(0, self.offset_x + dx))
        self.offset_y = min(max(0, max_y - self.height + self.PAN_MARGIN), max(0, self.offset_y + dy))
        get_app().invalidate()

    def geometry(self, height=None, arrows=False):
        total = max((p.y + self.NODE_HEIGHT + self.PAN_MARGIN for p in self.positions().values()), default=self.height)
        return ScrollbarGeometry(height or self.height, total, self.height, self.offset_y, arrows)

    def scroll(self, key):
        if key == KeyName.HOME:
            self.pan(-self.offset_x, -self.offset_y)
        elif key == KeyName.END:
            positions = list(self.positions().values())
            self.pan(max((p.x + self.NODE_WIDTH for p in positions), default=0),
                     max((p.y + self.NODE_HEIGHT for p in positions), default=0))
        else:
            delta = ({name: (x * self.PAN_ROWS, y * self.PAN_ROWS) for name, (x, y) in self.DIRECTIONS.items()}
                     | {KeyName.PAGE_UP: (0, -self.height), KeyName.PAGE_DOWN: (0, self.height)}).get(key)
            if delta:
                self.pan(*delta)

    def scroll_to(self, position):
        self.pan(0, position - self.offset_y)

    def reveal(self):
        if self.width < self.MIN_REVEAL_WIDTH or self.height < self.MIN_REVEAL_HEIGHT:
            return
        pos = self.positions().get(self.selected())
        if pos:
            self.offset_x = max(0, min(self.offset_x, pos.x))
            self.offset_y = max(0, min(self.offset_y, pos.y))
            self.offset_x = max(self.offset_x, pos.x + self.NODE_WIDTH - self.width + 1, 0)
            self.offset_y = max(self.offset_y, pos.y + self.NODE_HEIGHT - self.height + 1, 0)
        get_app().invalidate()

    def navigate(self, dx, dy):
        positions = self.positions()
        origin = positions.get(self.selected())
        if origin is None:
            if positions:
                self.select(next(iter(positions)))
            return
        candidates = []
        for name, pos in positions.items():
            x, y = pos.x - origin.x, pos.y - origin.y
            if x * dx + y * dy > 0:
                candidates.append((abs(x) + abs(y) * self.VERTICAL_DISTANCE_WEIGHT, name))
        if candidates:
            self.select(min(candidates)[1])
            self.reveal()

    def mouse_handler(self, event):
        if event.event_type in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
            self.pan(0, -self.PAN_ROWS if event.event_type == MouseEventType.SCROLL_UP else self.PAN_ROWS)
            return None
        if event.event_type == MouseEventType.MOUSE_DOWN and event.button == MouseButton.LEFT:
            get_app().layout.focus(self)
            name = self.hit(event.position.x, event.position.y)
            if name is not None and self.select(name) is not False:
                self.drag = (name, event.position, self.positions()[name])
                self.drag_position = None
            elif name is None:
                self.pan_anchor = (event.position, self.offset_x, self.offset_y)
            return None
        if self.pan_anchor is not None:
            start, x, y = self.pan_anchor
            if event.event_type == MouseEventType.MOUSE_MOVE:
                self.pan(x + start.x - event.position.x - self.offset_x,
                         y + start.y - event.position.y - self.offset_y)
                return None
            if event.event_type == MouseEventType.MOUSE_UP:
                self.pan_anchor = None
                return None
        if self.drag is not None:
            name, start, original = self.drag
            if event.event_type == MouseEventType.MOUSE_MOVE and event.button == MouseButton.LEFT:
                self.drag_position = NodePosition(max(0, original.x + event.position.x - start.x),
                                                  max(0, original.y + event.position.y - start.y))
                get_app().invalidate()
                return None
            if event.event_type == MouseEventType.MOUSE_UP:
                position = self.drag_position
                self.drag = self.drag_position = None
                if position is not None and position != original:
                    self.move_node(name, position.x, position.y)
                return None
        return NotImplemented

    def create_content(self, width, height):
        resized = (self.width, self.height) != (max(1, width), max(1, height))
        self.width, self.height = max(1, width), max(1, height)
        if resized:
            self.reveal()
        key = (self.draft.revision, self.draft.path, self.selected(), width, height,
               self.offset_x, self.offset_y, self.errors, self.drag_position)
        if key != self._cache_key:
            self._cache_key = key
            self._lines = self._render(width, height)
        return UIContent(get_line=lambda y: self._lines[y], line_count=len(self._lines),
                         cursor_position=Point(0, 0), show_cursor=False)

    def _render(self, width, height):
        # Allocate only visible terminal cells, never the potentially huge world.
        background = ThemeSelector.WORKFLOW_CANVAS.style
        cells = [[(background, self.SPACE) for _ in range(width)] for _ in range(height)]
        ox, oy = self.offset_x, self.offset_y
        muted = ThemeSelector.MUTED.style + ThemeStyle.MODIFIER_SEPARATOR + background

        def put(x, y, text, style=muted):
            for char in str(text).replace(self.NEWLINE, self.SPACE).replace(self.CARRIAGE_RETURN, self.SPACE):
                size = get_cwidth(char)
                if size < 1:
                    continue
                if 0 <= y - oy < height and 0 <= x - ox and x - ox + size <= width:
                    cells[y - oy][x - ox] = (style, char)
                    for index in range(1, size):
                        cells[y - oy][x - ox + index] = (style, '')
                x += size

        def horizontal(x1, x2, y):
            for x in range(max(min(x1, x2), ox), min(max(x1, x2), ox + width - 1) + 1):
                put(x, y, self.HORIZONTAL)

        def vertical(x, y1, y2):
            for y in range(max(min(y1, y2), oy), min(max(y1, y2), oy + height - 1) + 1):
                put(x, y, self.VERTICAL)

        def clipped(text, limit):
            result, used = '', 0
            for char in str(text).replace(self.NEWLINE, self.SPACE):
                size = get_cwidth(char)
                if size < 1:
                    continue
                if used + size > limit:
                    return result + self.ELLIPSIS
                result += char
                used += size
            return result

        positions = self.positions()
        graph = self.draft.graph()
        for edge in graph[WorkflowDocument.EDGES]:
            if (not isinstance(edge, dict) or not isinstance(edge.get(WorkflowEdge.SOURCE), str)
                    or not isinstance(edge.get(WorkflowEdge.TARGET), str)):
                continue
            a, b = positions.get(edge.get(WorkflowEdge.SOURCE)), positions.get(edge.get(WorkflowEdge.TARGET))
            if a is None or b is None:
                continue
            start, end, y1, y2 = a.x + self.NODE_WIDTH, b.x - 1, a.y + self.EDGE_ROW, b.y + self.EDGE_ROW
            middle = max(start + 1, (start + end) // 2)
            horizontal(start, middle, y1)
            vertical(middle, y1, y2)
            horizontal(middle, end, y2)
            put(end, y2, self.ARROW)
            if edge.get(WorkflowEdge.PORT):
                put(middle + 1, y2 - 1, clipped(edge[WorkflowEdge.PORT], self.PORT_CHARS))
        for name, pos in positions.items():
            if pos.y + self.NODE_HEIGHT < oy or pos.y >= oy + height or pos.x + self.NODE_WIDTH < ox or pos.x >= ox + width:
                continue
            style = (ThemeSelector.ACCENT.style + ThemeStyle.MODIFIER_SEPARATOR + ThemeStyle.BOLD if name == self.selected() else ThemeSelector.MUTED.style)
            if name in self.errors:
                style = ThemeSelector.SYSTEM.style + ThemeStyle.MODIFIER_SEPARATOR + ThemeStyle.BOLD
            style += ThemeStyle.MODIFIER_SEPARATOR + background
            put(pos.x, pos.y, self.TOP_LEFT + self.HORIZONTAL * (self.NODE_WIDTH - self.NODE_BORDER_COLUMNS) + self.TOP_RIGHT, style)
            for row in range(1, self.NODE_HEIGHT - 1):
                put(pos.x, pos.y + row, self.VERTICAL + self.SPACE * (self.NODE_WIDTH - self.NODE_BORDER_COLUMNS) + self.VERTICAL, style)
            put(pos.x, pos.y + self.NODE_HEIGHT - 1, self.BOTTOM_LEFT + self.HORIZONTAL * (self.NODE_WIDTH - self.NODE_BORDER_COLUMNS) + self.BOTTOM_RIGHT, style)
            marker = self.ENTRY_MARKER if name == graph.get(WorkflowDocument.ENTRY) else self.PLAIN_MARKER
            put(pos.x + 1, pos.y + 1, marker + clipped(self.draft.title(name), self.NODE_WIDTH - self.TITLE_RESERVED_COLUMNS), style)
            node = graph[WorkflowDocument.NODES][name]
            kind = node.get(WorkflowNode.TYPE, self.UNKNOWN_KIND) if isinstance(node, dict) else self.UNKNOWN_KIND
            put(pos.x + self.CONTENT_COLUMN, pos.y + self.TYPE_ROW, clipped(kind, self.NODE_WIDTH - self.CONTENT_RESERVED_COLUMNS), muted)
            put(pos.x + self.CONTENT_COLUMN, pos.y + self.ID_ROW, clipped(name, self.NODE_WIDTH - self.CONTENT_RESERVED_COLUMNS), muted)
        return [[part for part in row if part[1]] for row in cells]
