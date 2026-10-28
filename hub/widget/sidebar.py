"""Shared selection-on-navigation list for sessions and settings."""

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType, MouseButton
from prompt_toolkit.utils import get_cwidth

from ..asset import icon
from .section import section
from .input_contract import MouseResult
from .auxiliary import AuxiliaryBar
from ..asset.icon import AuxiliaryIcon
from ..ui.input.keys import KeyName
from ..config.theme_style import ThemeSelector


class SidebarHeading(HSplit):
    """Keep section titles and rules identical across pages and focus changes."""

    PREFIX = " "

    def __init__(self, title, *, actions=None):
        self.title = lambda: self.PREFIX + (title() if callable(title) else title)
        self.actions = actions
        if actions is not None:
            actions.width = actions.minimum_width
        super().__init__(
            [section(self.title, actions=actions)],
            style=ThemeSelector.SIDEBAR_HEADING.style,
        )

    @property
    def minimum_width(self):
        return get_cwidth(self.title()) + (
            1 + self.actions.minimum_width if self.actions is not None else 0
        )


class SidebarActions(AuxiliaryBar):
    """The same CRUD actions for project and session category headings."""

    ACTIONS = (
        (AuxiliaryIcon.CREATE, KeyName.C),
        (AuxiliaryIcon.CLONE, KeyName.R),
        (AuxiliaryIcon.EDIT, KeyName.EDIT),
        (AuxiliaryIcon.DELETE, KeyName.CLEAR),
    )

    def __init__(self, perform, icons, restore):
        super().__init__(
            [(name, lambda key=key: perform(key)) for name, key in self.ACTIONS],
            icons, restore,
        )


@dataclass(frozen=True, slots=True)
class SidebarItem:
    key: str
    title: str
    detail: str = ""
    margin_top: int = 0


class SidebarList(FormattedTextControl):
    NO_BINDINGS = None
    NO_ACTIVATE = None
    FOCUSABLE = True
    SHOW_CURSOR = False
    ROW_HEIGHT = 1
    DETAIL_HEIGHT = 3
    DETAIL_CLICK_HEIGHT = 2
    CURSOR_COLUMN = 0
    NEWLINE = "\n"
    UNSELECTED = " "
    TITLE_FORMAT = " {marker} {title}\n"
    DETAIL_FORMAT = "   {detail}\n\n"

    def __init__(
        self,
        items,
        selected,
        on_select,
        *,
        key_bindings=NO_BINDINGS,
        on_activate=NO_ACTIVATE
    ):
        self.items, self.selected, self.on_select = items, selected, on_select
        self.on_activate = on_activate
        self._click_focused = None
        super().__init__(
            self.fragments,
            focusable=self.FOCUSABLE,
            show_cursor=self.SHOW_CURSOR,
            key_bindings=key_bindings,
            get_cursor_position=self.cursor_position,
        )

    def move(self, direction):
        """Arrow movement stays inside this category, including its boundaries."""
        keys = [item.key for item in self.items()]
        if keys:
            index = keys.index(self.selected()) if self.selected() in keys else 0
            self.on_select(keys[max(0, min(len(keys) - 1, index + direction))])

    def activate(self):
        """Entering a category selects its remembered item or its first row."""
        keys = [item.key for item in self.items()]
        get_app().layout.focus(self)
        if keys:
            self.on_select(self.selected() if self.selected() in keys else keys[0])

    def cursor_position(self):
        line = 0
        for item in self.items():
            line += item.margin_top
            if item.key == self.selected():
                return Point(x=self.CURSOR_COLUMN, y=line)
            line += self.DETAIL_HEIGHT if item.detail else self.ROW_HEIGHT
        return Point(x=self.CURSOR_COLUMN, y=0)

    def begin_click(self, focused):
        """Capture focus before the host mouse router focuses the pressed Window."""
        self._click_focused = focused

    def mouse_handler(self, event):
        # Handle the entire row, including whitespace beyond its title.
        if (
            event.event_type == MouseEventType.MOUSE_UP
            and event.button == MouseButton.LEFT
        ):
            was_focused = (get_app().layout.current_control is self
                           if self._click_focused is None else self._click_focused)
            self._click_focused = None
            get_app().layout.focus(self)
            row = 0
            for item in self.items():
                row += item.margin_top
                height = self.DETAIL_HEIGHT if item.detail else self.ROW_HEIGHT
                if (
                    row
                    <= event.position.y
                    < row
                    + (self.DETAIL_CLICK_HEIGHT if item.detail else self.ROW_HEIGHT)
                ):
                    repeated = item.key == self.selected()
                    self.on_select(item.key)
                    if was_focused and repeated and self.on_activate:
                        self.on_activate()
                    return MouseResult.HANDLED
                row += height
            # Empty rows only focus the category; never select the last item.
            return MouseResult.HANDLED
        return MouseResult.UNHANDLED

    def fragments(self):
        fragments = []
        for item in self.items():
            if item.margin_top:
                fragments.append(
                    (ThemeSelector.SIDEBAR.style, self.NEWLINE * item.margin_top)
                )
            selected = item.key == self.selected()
            fragments.append(
                (
                    (
                        ThemeSelector.SELECTED.style
                        if selected
                        else ThemeSelector.SIDEBAR.style
                    ),
                    self.TITLE_FORMAT.format(
                        marker=icon.SELECTED if selected else self.UNSELECTED,
                        title=item.title,
                    ),
                )
            )
            if item.detail:
                fragments.append(
                    (
                        ThemeSelector.MUTED.style,
                        self.DETAIL_FORMAT.format(detail=item.detail),
                    )
                )
        if fragments:
            style, text = fragments[-1]
            fragments[-1] = (style, text.rstrip(self.NEWLINE))
        return fragments
