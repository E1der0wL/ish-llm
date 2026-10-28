"""Hub-local widget appearance without changing host or PTK defaults."""

from time import monotonic

from prompt_toolkit.layout import ScrollablePane as PTKScrollablePane
from prompt_toolkit.layout.margins import ScrollbarMargin as PTKScrollbarMargin
from prompt_toolkit.layout.margins import ConditionalMargin
from prompt_toolkit.layout.menus import CompletionsMenu as PTKCompletionsMenu
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.widgets import Button as PTKButton
from prompt_toolkit.widgets import (
    RadioList as PTKRadioList,
    TextArea as PTKTextArea,
    CheckboxList as PTKCheckboxList,
)
from ..asset import icon
from ..ui.input.registry import ShortcutRegistry
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.application.current import get_app
from .mouse import ScrollbarGeometry, hover_style
from .input_contract import EditableControl
from .focus import focus_control
from .mouse_contract import MouseBinding, ControlRole
from ..config.theme_style import ThemeSelector, ThemeStyle
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel


def _arrows(window) -> None:
    def shared(margin):
        if isinstance(margin, ConditionalMargin):
            return ConditionalMargin(shared(margin.margin), margin.filter)
        if isinstance(margin, PTKScrollbarMargin) and not isinstance(margin, ScrollbarMargin):
            return ScrollbarMargin(display_arrows=margin.display_arrows)
        return margin

    for margins in (window.left_margins, window.right_margins):
        for index, margin in enumerate(margins):
            margins[index] = shared(margin)


@MouseBinding.register_interactive
class ScrollbarMargin(PTKScrollbarMargin):
    MIN_HEIGHT = 1
    EMPTY_STYLE = ""
    NEWLINE = "\n"
    FIRST_ROW = 0
    NO_HOVER = False

    def __init__(self, **kwargs):
        super().__init__(
            up_arrow_symbol=icon.SCROLL_UP, down_arrow_symbol=icon.SCROLL_DOWN, **kwargs
        )

    def create_margin(self, window_render_info, width, height):
        if not width or height < self.MIN_HEIGHT:
            return []
        geometry = ScrollbarGeometry(
            height,
            window_render_info.content_height,
            window_render_info.window_height,
            window_render_info.vertical_scroll,
            self.display_arrows(),
        )
        rows = scrollbar_rows(geometry, MouseBinding.hovered(window_render_info.window))
        return [
            fragment
            for i, row in enumerate(rows)
            for fragment in (
                [row, (self.EMPTY_STYLE, self.NEWLINE)] if i < len(rows) - 1 else [row]
            )
        ]


class CompletionsMenu(PTKCompletionsMenu):
    """PTK completion behavior with Hub's shared scrollbar and mouse geometry."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _arrows(self.content)


def scrollbar_rows(geometry, hovered=ScrollbarMargin.NO_HOVER):
    rows = []
    for row in range(geometry.height):
        if geometry.edge and row in (ScrollbarMargin.FIRST_ROW, geometry.height - 1):
            rows.append(
                (
                    ThemeSelector.SCROLLBAR_ARROW.style,
                    (
                        icon.SCROLL_UP
                        if row == ScrollbarMargin.FIRST_ROW
                        else icon.SCROLL_DOWN
                    ),
                )
            )
        elif geometry.top <= row < geometry.top + geometry.thumb:
            rows.append(
                (
                    ThemeSelector.SCROLLBAR_THUMB.style
                    + (
                        ThemeStyle.MODIFIER_SEPARATOR
                        + ThemeSelector.SCROLLBAR_HOVER.style
                        if hovered
                        else ScrollbarMargin.EMPTY_STYLE
                    ),
                    icon.SCROLL_THUMB,
                )
            )
        else:
            rows.append((ThemeSelector.SCROLLBAR_TRACK.style, icon.SCROLL_TRACK))
    return rows


class ScrollablePane(PTKScrollablePane):
    INITIAL_METRICS = (1, 1)
    ABSENT = None
    ARROWS = True
    BUFFER_ATTRIBUTE = "buffer"
    TOP = 0
    MIN_PAGE = 1
    PAGE_OVERLAP = 2
    LINE_UP = -1
    LINE_DOWN = 1

    def __init__(self, content, **kwargs):
        super().__init__(
            content,
            up_arrow_symbol=icon.SCROLL_UP,
            down_arrow_symbol=icon.SCROLL_DOWN,
            **kwargs
        )
        self._mouse_metrics = self.INITIAL_METRICS
        self._mouse_anchor = self.ABSENT
        self._scrollbar_parent_style = ThemeSelector.ROOT.style

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        # Unlike a Window margin, PTK's pane scrollbar is drawn separately.
        # Carry the containing surface's style into that final rendering pass.
        self._scrollbar_parent_style = parent_style
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)

    def geometry(self, height=ABSENT, arrows=ARROWS):
        total, visible = self._mouse_metrics
        return ScrollbarGeometry(
            height or visible, total, visible, self.vertical_scroll, arrows
        )

    def scroll_to(self, position):
        self._mouse_anchor = self._focus_anchor()
        self.vertical_scroll = max(self.TOP, min(self.geometry().limit, position))

    def scroll(self, key):
        geometry = self.geometry()
        if key in (KeyName.HOME, KeyName.END):
            self.scroll_to(self.TOP if key == KeyName.HOME else geometry.limit)
        elif key in (KeyName.UP, KeyName.DOWN, KeyName.PAGE_UP, KeyName.PAGE_DOWN):
            page = max(self.MIN_PAGE, geometry.visible - self.PAGE_OVERLAP)
            self.scroll_to(
                geometry.offset
                + {
                    KeyName.UP: self.LINE_UP,
                    KeyName.DOWN: self.LINE_DOWN,
                    KeyName.PAGE_UP: -page,
                    KeyName.PAGE_DOWN: page,
                }[key]
            )
        # Fields wrap to the available width; there is no horizontal overflow.

    def _focus_anchor(self):
        app = get_app()
        control = app.layout.current_control
        buffer = getattr(control, self.BUFFER_ATTRIBUTE, self.ABSENT)
        return control, buffer.cursor_position if buffer else self.ABSENT

    def _make_window_visible(
        self, visible_height, virtual_height, visible_win_write_pos, cursor_position
    ):
        if (
            self._mouse_anchor is not self.ABSENT
            and self._mouse_anchor == self._focus_anchor()
        ):
            self.vertical_scroll = max(
                self.TOP, min(virtual_height - visible_height, self.vertical_scroll)
            )
            return
        self._mouse_anchor = self.ABSENT
        super()._make_window_visible(
            visible_height, virtual_height, visible_win_write_pos, cursor_position
        )

    def _copy_over_write_positions(self, screen, temp_screen, write_position):
        super()._copy_over_write_positions(screen, temp_screen, write_position)
        for window in temp_screen.visible_windows_to_write_positions:
            MouseBinding.set_clip(window, write_position)
            MouseBinding.set_pane(window, self)

    def _draw_scrollbar(self, write_position, content_height, screen) -> None:
        self._mouse_metrics = (content_height, write_position.height)
        mouse = MouseBinding.controller(get_app())
        if mouse is not self.ABSENT:
            mouse.record_pane(self, write_position, screen)
        xpos = write_position.xpos + write_position.width - 1
        for row, (style, char) in enumerate(
            scrollbar_rows(self.geometry(), MouseBinding.hovered(self))
        ):
            screen.data_buffer[write_position.ypos + row][xpos] = Char(
                char, ThemeStyle.joined(self._scrollbar_parent_style, style)
            )


class Button(PTKButton):
    LEFT_SYMBOL = "left_symbol"
    RIGHT_SYMBOL = "right_symbol"
    NO_SYMBOL = ""
    SHOW_CURSOR = False
    ACTIVATION_FEEDBACK_SECONDS = 0.15

    @property
    def text(self):
        return self._text() if callable(self._text) else self._text

    @text.setter
    def text(self, value):
        self._text = value

    def _get_text_fragments(self):
        from prompt_toolkit.mouse_events import MouseButton, MouseEventType

        def activate(event):
            if event.button == MouseButton.LEFT:
                if event.event_type == MouseEventType.MOUSE_DOWN:
                    self._feedback()
                elif event.event_type == MouseEventType.MOUSE_UP:
                    self._activate()

        text_style = 'class:button.text'
        if monotonic() < self._pressed_until:
            text_style += ' ' + ThemeSelector.BUTTON_PRESSED.style

        # WindowAlign.CENTER is the sole alignment owner. Never pre-pad to the
        # preferred width: the parent may allocate a different number of cells.
        return [
            ("class:button.arrow", self.left_symbol, activate),
            ("[SetCursorPosition]", ""),
            (text_style, self.text, activate),
            ("class:button.arrow", self.right_symbol, activate),
        ]

    def __init__(self, *args, **kwargs):
        self._pressed_until = 0.0
        kwargs.setdefault(self.LEFT_SYMBOL, self.NO_SYMBOL)
        kwargs.setdefault(self.RIGHT_SYMBOL, self.NO_SYMBOL)
        super().__init__(*args, **kwargs)
        self.control.show_cursor = self.SHOW_CURSOR
        ControlRole.register(self.control, ControlRole.BUTTON)
        hover_style(self.window)
        MouseBinding.set_hover_focus(self.window, self.focus_from_hover)
        registry = ShortcutRegistry()
        registry.add(
            [KeyName.ENTER, KeyName.SPACE],
            ShortcutLabel.ENTER_SPACE,
            ShortcutAction.ACTIVATE,
            lambda e: self._activate(),
        )
        registry.attach(self.control)
        self.control.key_bindings = merge_key_bindings(
            [self.control.key_bindings, registry.bindings]
        )

    def focus_from_hover(self):
        focus_control(self)

    def _activate(self):
        self._feedback()
        if self.handler:
            return self.handler()

    def _feedback(self):
        self._pressed_until = monotonic() + self.ACTIVATION_FEEDBACK_SECONDS
        app = get_app()
        app.invalidate()
        # Terminals have no keyboard-release event. Feedback never delays the
        # action and expires equally for Enter, Space and a mouse activation.
        if app.is_running:
            app.loop.call_later(self.ACTIVATION_FEEDBACK_SECONDS, app.invalidate)


class ChoiceBinding:
    NO_CALLBACK = None


def _bind_choices(widget, *, on_move=ChoiceBinding.NO_CALLBACK):
    _arrows(widget.window)
    hover_style(widget.window)
    ControlRole.register(widget.control, ControlRole.CHOICES)
    native = widget.control.key_bindings
    registry = ShortcutRegistry()

    def invoke(event):
        keys = tuple(item.key for item in event.key_sequence)
        for binding in reversed(native.get_bindings_for_keys(keys)):
            if binding.filter():
                result = binding.handler(event)
                # Enter/Space and mouse clicks notify through _handle_enter.
                if on_move and keys[-1] in (KeyName.UP, KeyName.DOWN):
                    on_move()
                return result

    registry.add(
        [KeyName.UP, KeyName.DOWN],
        ShortcutLabel.VERTICAL,
        ShortcutAction.SELECT,
        invoke,
    )
    registry.add(
        [KeyName.ENTER, KeyName.SPACE],
        ShortcutLabel.ENTER_SPACE,
        ShortcutAction.CONFIRM,
        invoke,
    )
    registry.attach(widget.control)
    widget.control.key_bindings = merge_key_bindings([native, registry.bindings])


class RadioList(PTKRadioList):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bind_choices(self)


class CheckboxList(PTKCheckboxList):
    """Shared multi-selection control with a separate highlighted-row callback."""

    def __init__(self, *args, on_select=ChoiceBinding.NO_CALLBACK, **kwargs):
        self.on_select = on_select
        super().__init__(*args, **kwargs)
        _bind_choices(self, on_move=self.notify)

    @property
    def highlighted(self):
        return self.values[self._selected_index][0]

    def notify(self):
        if self.on_select:
            self.on_select(self.highlighted)

    def _handle_enter(self):
        super()._handle_enter()
        self.notify()


class TextArea(PTKTextArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _arrows(self.window)
        hover_style(self.window)
        EditableControl.register(self.control)
        if not self.buffer.read_only():
            self.buffer.cursor_position = len(self.buffer.text)
        native_mouse = self.control.mouse_handler

        def mouse(event):
            result = native_mouse(event)
            if self.buffer.selection_state and not self.buffer.read_only():
                self.buffer.selection_state.enter_shift_mode()
            return result

        self.control.mouse_handler = mouse

    def insert_text(self, text):
        if self.buffer.selection_state:
            self.buffer.cut_selection()
        self.buffer.insert_text(text)
