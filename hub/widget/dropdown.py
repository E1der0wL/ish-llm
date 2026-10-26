"""A single-selection dropdown with explicit commit and cancel semantics."""

from hub.ui.input.registry import ShortcutRegistry
from hub.ui.input.keys import KeyName, ShortcutLabel, ShortcutAction


from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.formatted_text import to_formatted_text, fragment_list_width, fragment_list_to_text
from prompt_toolkit.mouse_events import MouseEventType

from .controls import Button, RadioList
from ..asset.icon import NERD


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
                if event.event_type == MouseEventType.MOUSE_UP:
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
    MENU_BOTTOM = 1
    MENU_LEFT = 0
    MENU_MAX_ROWS = 10
    MENU_BORDER_ROWS = 0
    MENU_MIN_ROWS = 1
    TERMINAL_RESERVED_ROWS = 5
    MENU_LAYER = 400
    BASE_LAYER = 0
    MIN_CAPTION_WIDTH = 1
    CAPTION_RESERVED_WIDTH = 4
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
        self.width = width
        self.button = Button(self.caption, handler=self.open, width=width)
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
        super().__init__(
            self.button,
            floats=[
                Float(
                    ConditionalContainer(self.choices, Condition(self.is_open)),
                    # Explicit height lets the menu extend above a one-row button;
                    # preferred height alone is clipped to the button's own row.
                    bottom=self.MENU_BOTTOM,
                    left=self.MENU_LEFT,
                    width=self.rendered_width,
                    height=lambda: min(
                        self.MENU_MAX_ROWS,
                        len(self.choices.values) + self.MENU_BORDER_ROWS,
                        max(
                            self.MENU_MIN_ROWS,
                            get_app().output.get_size().rows
                            - self.TERMINAL_RESERVED_ROWS,
                        ),
                    ),
                    z_index=self.MENU_LAYER,
                )
            ],
            z_index=self.BASE_LAYER,
        )

    @property
    def control(self):
        return self.button.control

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

    def caption(self):
        from ..ui.output.labels import ellipsize

        label = next(
            (label for key, label in self.values() if key == self.value),
            self.placeholder,
        )
        return (
            ellipsize(
                label,
                max(self.MIN_CAPTION_WIDTH, self.width - self.CAPTION_RESERVED_WIDTH),
            )
            + self.CAPTION_SEPARATOR
            + self.icons().dropdown
        )

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
