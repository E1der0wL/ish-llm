"""A single-selection dropdown with explicit commit and cancel semantics."""

from functools import partial

from hub.ui.input.registry import ShortcutRegistry
from hub.ui.input.keys import KeyName, ShortcutLabel, ShortcutAction


from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer, Window
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.formatted_text import to_formatted_text, fragment_list_width, fragment_list_to_text
from prompt_toolkit.mouse_events import MouseButton, MouseEventType

from .controls import Button, RadioList, ScrollbarMargin, ScrollablePane
from .mouse_contract import MouseBinding
from ..asset.icon import NERD
from ..config.theme_style import ThemeSelector


class CompletionChoices(RadioList):
    """Dropdown interaction with the same borderless chrome as PTK completion."""
    MENU = 'class:completion-menu'
    NORMAL = 'class:completion-menu.completion'
    CURRENT = 'class:completion-menu.completion.current'
    ROW_INSET = 1
    SCROLLBAR_WIDTH = 1
    MINIMUM_WIDTH = 1
    TEXT_POSITION = 1
    SPACE = ' '
    NEWLINE = '\n'
    EMPTY_TEXT = ''
    CURSOR_POSITION = '[SetCursorPosition]'
    SHOW_CURSOR = False
    SHOW_ARROWS = False

    def __init__(self, values, width):
        self.menu_width = width
        super().__init__(values, container_style=self.MENU, show_cursor=self.SHOW_CURSOR)
        self.window.right_margins = [ScrollbarMargin(display_arrows=self.SHOW_ARROWS)]

    def _get_text_fragments(self):
        from ..ui.output.labels import ellipsize

        result = []
        menu_width = self.menu_width() if callable(self.menu_width) else self.menu_width
        width = max(self.MINIMUM_WIDTH, menu_width - self.SCROLLBAR_WIDTH)
        for index, (_, label) in enumerate(self.values):
            style = self.CURRENT if index == self._selected_index else self.NORMAL

            def clicked(event, row=index):
                if (event.event_type == MouseEventType.MOUSE_MOVE
                        and event.button == MouseButton.NONE):
                    # Hover changes only the pending keyboard row. Commit and
                    # cancellation still belong to the containing dropdown.
                    if self._selected_index != row:
                        self._selected_index = row
                        get_app().invalidate()
                elif event.event_type == MouseEventType.MOUSE_UP:
                    self._selected_index = row
                    self._handle_enter()

            if index == self._selected_index:
                result.append((self.CURSOR_POSITION, self.EMPTY_TEXT, clicked))
            fragments = to_formatted_text(ellipsize(
                fragment_list_to_text(to_formatted_text(label)),
                width - self.ROW_INSET - self.ROW_INSET,
            ))
            result.append((style, self.SPACE * self.ROW_INSET, clicked))
            result.extend((style, item[self.TEXT_POSITION], clicked) for item in fragments)
            result.append((style, self.SPACE * max(self.ROW_INSET, width - self.ROW_INSET - fragment_list_width(fragments)), clicked))
            if index != len(self.values) - self.ROW_INSET:
                result.append((self.EMPTY_TEXT, self.NEWLINE, clicked))
        return result


class DropdownBox(FloatContainer):
    ABSENT = None
    EMPTY_LABEL = ""
    CLOSED = False
    EXPANDED = True
    DEFAULT_WIDTH = 16
    CHOICE_ROWS = 8
    MENU_LEFT = 0
    MENU_LAYER = 400
    BASE_LAYER = 0
    MIN_CAPTION_WIDTH = 1
    CAPTION_SEPARATOR = " "
    FIRST_CHOICE = 0

    def __init__(
        self,
        items,
        *,
        value=ABSENT,
        placeholder=EMPTY_LABEL,
        on_change=ABSENT,
        width=DEFAULT_WIDTH,
        icons=lambda: NERD
    ):
        self.items, self.value, self.placeholder = items, value, placeholder
        self.icons = icons
        self.on_change, self.expanded = on_change, self.CLOSED
        self.button = Button(self.caption, handler=self.open, width=width,
                             right_symbol=self.indicator)
        self.choices = CompletionChoices([(self.ABSENT, self.EMPTY_LABEL)], self.rendered_width)
        self.choices.window.height = self.CHOICE_ROWS
        keys = ShortcutRegistry.require(self.choices.control)
        keys.add(
            [KeyName.ENTER, KeyName.SPACE],
            ShortcutLabel.ENTER_SPACE,
            ShortcutAction.CONFIRM,
            lambda e: self.commit(),
        )
        keys.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.CLOSE,
            lambda e: self.close(),
        )
        keys.add(
            [KeyName.TAB, KeyName.SHIFT_TAB],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.ABSENT,
        )
        self.choices._handle_enter = self.commit
        MouseBinding.set_click_away(self.choices.control, self.close)
        super().__init__(
            self.button,
            floats=[
                Float(
                    ConditionalContainer(self.choices, Condition(self.local_menu)),
                    # Position/height are assigned from the current visible area,
                    # rather than this one-row container's own dimensions.
                    top=0,
                    left=self.MENU_LEFT,
                    width=self.rendered_width,
                    height=0,
                    z_index=self.MENU_LAYER,
                )
            ],
            z_index=self.BASE_LAYER,
            style=ThemeSelector.DROPDOWN.style,
        )

    @property
    def control(self):
        return self.button.control

    @property
    def width(self):
        return self.button.width

    @property
    def controls(self):
        return self.button.control, self.choices.control

    def values(self):
        return list(self.items() if callable(self.items) else self.items)

    def is_open(self):
        if self.expanded and get_app().layout.current_control not in self.controls:
            self.expanded = self.CLOSED
        return self.expanded

    def rendered_width(self):
        info = self.button.window.render_info
        return info.window_width if info else self.width

    def local_menu(self):
        if not self.is_open():
            return self.CLOSED
        layout = get_app().layout
        parent = layout.get_parent(self)
        while parent is not self.ABSENT:
            if isinstance(parent, DropdownLayer):
                return self.CLOSED
            parent = layout.get_parent(parent)
        return self.EXPANDED

    @classmethod
    def focused(cls):
        layout = get_app().layout
        parent = layout.current_window
        while parent is not cls.ABSENT:
            if isinstance(parent, cls):
                return parent
            parent = layout.get_parent(parent)
        return cls.ABSENT

    def menu_geometry(self, anchor, top, bottom):
        above = max(0, anchor.ypos - top)
        below = max(0, bottom - anchor.ypos - anchor.height)
        preferred = min(self.CHOICE_ROWS, len(self.choices.values))
        opens_below = below >= preferred or (above < preferred and below >= above)
        height = min(preferred, below if opens_below else above)
        return (anchor.height if opens_below else -height), height

    def visible_bounds(self):
        """A pane renders in virtual coordinates; ordinary layouts use screen rows."""
        layout = get_app().layout
        parent = layout.get_parent(self)
        while parent is not None:
            if isinstance(parent, ScrollablePane):
                return parent.vertical_scroll, parent.vertical_scroll + parent.viewport_height
            parent = layout.get_parent(parent)
        return 0, get_app().output.get_size().rows

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        if self.local_menu():
            top, bottom = self.visible_bounds()
            menu = self.floats[0]
            menu.top, menu.height = self.menu_geometry(write_position, top, bottom)
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)

    def caption(self):
        label = next(
            (label for key, label in self.values() if key == self.value),
            self.placeholder,
        )
        return label

    def indicator(self):
        return self.CAPTION_SEPARATOR + self.icons().dropdown

    def open(self):
        values = self.values()
        if not values:
            return
        self.choices.values = values
        self.choices._selected_index = next(
            (i for i, (key, _) in enumerate(values) if key == self.value),
            self.FIRST_CHOICE,
        )
        self.choices.current_value = self.value
        self.choices.window.height = min(self.CHOICE_ROWS, len(values))
        self.expanded = self.EXPANDED
        get_app().layout.focus(self.choices)
        get_app().invalidate()

    def close(self):
        self.expanded = self.CLOSED
        get_app().layout.focus(self.button)
        get_app().invalidate()

    def commit(self):
        value = self.choices.values[self.choices._selected_index][0]
        self.close()
        if self.on_change:
            self.on_change(value)
        else:
            self.value = value


class DropdownMenu(Window):
    """Paint after panes/dialogs, using the button's final screen coordinates."""

    def _draw(self, screen, mouse_handlers, write_position, erase_bg):
        dropdown = DropdownBox.focused()
        if dropdown is None or not dropdown.is_open():
            return
        anchor = screen.visible_windows_to_write_positions.get(dropdown.button.window)
        if anchor is None:
            return
        clip = MouseBinding.clip(dropdown.button.window)
        if clip is not None and not clip.ypos <= anchor.ypos < clip.ypos + clip.height:
            return
        top, height = dropdown.menu_geometry(
            anchor, write_position.ypos, write_position.ypos + write_position.height,
        )
        if not height:
            return
        # The menu is a screen overlay, not scrollable-pane content. The local
        # container retains its control/keys so modal scope and focus stay intact.
        MouseBinding.set_clip(dropdown.choices.window, None)
        MouseBinding.set_pane(dropdown.choices.window, None)
        dropdown.choices.__pt_container__().write_to_screen(
            screen, mouse_handlers,
            WritePosition(anchor.xpos, anchor.ypos + top, anchor.width, height),
            ThemeSelector.DROPDOWN.style, erase_bg, None,
        )

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        draw = partial(self._draw, screen, mouse_handlers, write_position, erase_bg)
        if z_index is None or z_index <= DropdownBox.BASE_LAYER:
            draw()
        else:
            screen.draw_with_z_index(z_index, draw)


class DropdownLayer(FloatContainer):
    """One shared menu surface for all page and popup dropdowns beneath it."""

    EDGE = 0

    def __init__(self, content):
        super().__init__(content, floats=[Float(
            DropdownMenu(), top=self.EDGE, bottom=self.EDGE, left=self.EDGE, right=self.EDGE,
            z_index=DropdownBox.MENU_LAYER,
        )])
