"""Shared tag navigation; engine outputs supply entries and their actions."""

from hub.locales.keys import MessageKey

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth

from ..sidebar import SidebarItem, SidebarList
from ..clipboard import copy_text
from ..controls import Button
from ...ui.output.labels import ellipsize
from ...ui.input.registry import ShortcutRegistry
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...config.theme_style import ThemeSelector, ThemeStyle


@dataclass(frozen=True, slots=True)
class OutputTag:
    id: str
    title: str
    position: int
    icon: str = ""
    raw: str | None = None
    indent: int = 0
    margin_top: int = 0


class TagBar:
    ABSENT = None
    HIDDEN = False
    VISIBLE = True
    TRACKING = True
    MANUAL = False
    UNSELECTED = ""
    MODAL = True
    WRAP = False
    COLLAPSE_WIDTH = 3
    HEADER_HEIGHT = 1
    DIVIDER_WIDTH = 1
    DIVIDER = "│"
    SPACE = " "
    ICON_SPACING = "  "
    MIN_WIDTH = 1
    PREFERRED_MIN_WIDTH = 14
    MAX_WIDTH = 32
    AVAILABLE_FRACTION = 3
    RESERVED_CELLS = 4
    FIRST = 0
    PREVIOUS = -1
    NEXT = 1

    def __init__(self, output, view):
        self.output = output
        self.view, self.visible, self.selected = view, TagBar.HIDDEN, TagBar.UNSELECTED
        self.tracking = TagBar.TRACKING
        registry = ShortcutRegistry()
        registry.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.CLOSE,
            lambda e: self.close(),
        )
        registry.add(
            [KeyName.TAB, KeyName.SPACE, KeyName.ENTER],
            ShortcutLabel.TAB_SPACE_ENTER,
            ShortcutAction.EXPAND,
            lambda e: self.expand(),
        )
        registry.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.VERTICAL,
            ShortcutAction.SELECT,
            lambda e: self.move(
                TagBar.PREVIOUS if e.key_sequence[-1].key == KeyName.UP else TagBar.NEXT
            ),
        )
        registry.add(
            [KeyName.C],
            KeyName.C,
            ShortcutAction.COPY,
            lambda e: e.app.create_background_task(self.copy()),
        )
        self.control = SidebarList(
            self.items,
            lambda: self.selected,
            self.select,
            key_bindings=registry.bindings,
        )
        registry.attach(self.control)
        self.control.modal = TagBar.MODAL
        self.window = Window(
            self.control, wrap_lines=TagBar.WRAP, style=ThemeSelector.SIDEBAR.style
        )
        self.collapse = Button(
            lambda: view.icons.collapse, handler=self.close, width=TagBar.COLLAPSE_WIDTH
        )
        ShortcutRegistry.require(self.collapse.control).add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.CLOSE,
            lambda e: self.close(),
        )
        self.heading = Window(
            FormattedTextControl(lambda: TagBar.SPACE + view.t(MessageKey.TAGS_TITLE)),
            height=TagBar.HEADER_HEIGHT,
            style=ThemeStyle.joined(ThemeSelector.ACCENT.style, ThemeStyle.BOLD),
        )
        self.header = VSplit([self.heading, self.collapse], height=TagBar.HEADER_HEIGHT)
        self.contents = HSplit([self.header, self.window])
        self.container = ConditionalContainer(
            VSplit(
                [
                    Window(
                        width=TagBar.DIVIDER_WIDTH,
                        char=TagBar.DIVIDER,
                        style=ThemeSelector.DIVIDER.style,
                    ),
                    self.contents,
                ],
                width=self.width,
            ),
            Condition(lambda: self.visible),
        )

    def width(self):
        available = self.output.available_width
        return min(
            max(TagBar.MIN_WIDTH, available - TagBar.DIVIDER_WIDTH),
            max(
                TagBar.PREFERRED_MIN_WIDTH,
                min(TagBar.MAX_WIDTH, available // TagBar.AVAILABLE_FRACTION),
            ),
        )

    def entries(self):
        return sorted(self.output.tag_entries(), key=lambda item: item.position)

    def track_position(self):
        self.tracking = TagBar.TRACKING
        entries = self.entries()
        self.selected = (
            min(
                entries,
                key=lambda item: abs(item.position - self.output.tag_position()),
            ).id
            if entries
            else TagBar.UNSELECTED
        )

    def items(self):
        entries = self.entries()
        if self.tracking or self.selected not in {item.id for item in entries}:
            position = self.output.tag_position()
            self.selected = (
                min(entries, key=lambda item: abs(item.position - position)).id
                if entries
                else TagBar.UNSELECTED
            )
        # Divider, selection marker and surrounding spaces occupy four cells.
        width = max(TagBar.MIN_WIDTH, self.width() - TagBar.RESERVED_CELLS)
        items = []
        for entry in entries:
            prefix = TagBar.SPACE * entry.indent + (
                entry.icon + TagBar.ICON_SPACING if entry.icon else TagBar.UNSELECTED
            )
            items.append(
                SidebarItem(
                    entry.id,
                    prefix + ellipsize(entry.title, width - get_cwidth(prefix)),
                    margin_top=entry.margin_top,
                )
            )
        return items or [
            SidebarItem(TagBar.UNSELECTED, self.view.t(MessageKey.TAGS_EMPTY))
        ]

    def open(self):
        self.view.composer.buffer.cancel_completion()
        self.output.show_sidebar(self)
        self.visible = TagBar.VISIBLE
        self.track_position()
        get_app().layout.focus(self.control)
        get_app().invalidate()

    def close(self):
        self.visible = TagBar.HIDDEN
        self.view._focus_composer()
        get_app().invalidate()

    def select(self, identifier):
        self.selected = identifier
        self.tracking = TagBar.MANUAL
        item = next(
            (item for item in self.entries() if item.id == identifier), TagBar.ABSENT
        )
        if item:
            self.output.select_tag(item)
        self.tracking = TagBar.MANUAL
        get_app().invalidate()

    def move(self, direction):
        items = self.items()
        index = next(
            (index for index, item in enumerate(items) if item.key == self.selected),
            TagBar.FIRST,
        )
        self.select(items[max(TagBar.FIRST, min(len(items) - 1, index + direction))].key)

    def expand(self):
        item = next(
            (item for item in self.entries() if item.id == self.selected), TagBar.ABSENT
        )
        if item:
            self.output.expand_tag(item)

    async def copy(self):
        item = next(
            (item for item in self.entries() if item.id == self.selected), TagBar.ABSENT
        )
        if item and item.raw is not TagBar.ABSENT:
            try:
                await copy_text(item.raw)
            except (OSError, RuntimeError) as error:
                self.view.notice = self.view.t(MessageKey.ERROR, error=error)
            get_app().invalidate()
