"""Hub-local widget appearance without changing host or PTK defaults."""

from prompt_toolkit.layout import ScrollablePane as PTKScrollablePane
from prompt_toolkit.layout.margins import ScrollbarMargin as PTKScrollbarMargin
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.widgets import Button as PTKButton
from prompt_toolkit.widgets import RadioList as PTKRadioList, TextArea as PTKTextArea, CheckboxList as PTKCheckboxList
from ..asset import icon
from ..ui.input.registry import ShortcutRegistry
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.application.current import get_app
from .mouse import ScrollbarGeometry, hover_style
from .input_contract import EditableControl


def _arrows(window) -> None:
    for margins in (window.left_margins, window.right_margins):
        for index, margin in enumerate(margins):
            if isinstance(margin, PTKScrollbarMargin):
                margins[index] = ScrollbarMargin(display_arrows=margin.display_arrows)


class ScrollbarMargin(PTKScrollbarMargin):
    hub_interactive = True
    def __init__(self, **kwargs):
        super().__init__(up_arrow_symbol=icon.SCROLL_UP, down_arrow_symbol=icon.SCROLL_DOWN, **kwargs)

    def create_margin(self, window_render_info, width, height):
        if not width or height < 1:
            return []
        geometry = ScrollbarGeometry(height, window_render_info.content_height,
            window_render_info.window_height, window_render_info.vertical_scroll, self.display_arrows())
        rows = scrollbar_rows(geometry, getattr(window_render_info.window, "hub_mouse_hover", False))
        return [fragment for i, row in enumerate(rows)
                for fragment in ([row, ("", "\n")] if i < len(rows) - 1 else [row])]


def scrollbar_rows(geometry, hovered=False):
    rows = []
    for row in range(geometry.height):
        if geometry.edge and row in (0, geometry.height - 1):
            rows.append(("class:hub.scrollbar-arrow", icon.SCROLL_UP if row == 0 else icon.SCROLL_DOWN))
        elif geometry.top <= row < geometry.top + geometry.thumb:
            rows.append(("class:hub.scrollbar-thumb" + (" class:hub.scrollbar-hover" if hovered else ""), icon.SCROLL_THUMB))
        else:
            rows.append(("class:hub.scrollbar-track", icon.SCROLL_TRACK))
    return rows


class ScrollablePane(PTKScrollablePane):
    def __init__(self, content, **kwargs):
        super().__init__(content, up_arrow_symbol=icon.SCROLL_UP, down_arrow_symbol=icon.SCROLL_DOWN, **kwargs)
        self._mouse_metrics = (1, 1)
        self._mouse_anchor = None

    def geometry(self, height=None, arrows=True):
        total, visible = self._mouse_metrics
        return ScrollbarGeometry(height or visible, total, visible, self.vertical_scroll, arrows)

    def scroll_to(self, position):
        self._mouse_anchor = self._focus_anchor()
        self.vertical_scroll = max(0, min(self.geometry().limit, position))

    def scroll(self, key):
        geometry = self.geometry()
        if key in ("home", "end"):
            self.scroll_to(0 if key == "home" else geometry.limit)
        elif key in ("up", "down", "pageup", "pagedown"):
            page = max(1, geometry.visible - 2)
            self.scroll_to(geometry.offset + {"up": -1, "down": 1, "pageup": -page, "pagedown": page}[key])
        # Fields wrap to the available width; there is no horizontal overflow.

    def _focus_anchor(self):
        app = get_app()
        control = app.layout.current_control
        buffer = getattr(control, "buffer", None)
        return control, buffer.cursor_position if buffer else None

    def _make_window_visible(self, visible_height, virtual_height, visible_win_write_pos, cursor_position):
        if self._mouse_anchor is not None and self._mouse_anchor == self._focus_anchor():
            self.vertical_scroll = max(0, min(virtual_height - visible_height, self.vertical_scroll))
            return
        self._mouse_anchor = None
        super()._make_window_visible(visible_height, virtual_height, visible_win_write_pos, cursor_position)

    def _copy_over_write_positions(self, screen, temp_screen, write_position):
        super()._copy_over_write_positions(screen, temp_screen, write_position)
        for window in temp_screen.visible_windows_to_write_positions:
            window.hub_mouse_clip = write_position
            window.hub_mouse_pane = self

    def _draw_scrollbar(self, write_position, content_height, screen) -> None:
        self._mouse_metrics = (content_height, write_position.height)
        mouse = getattr(get_app(), "hub_mouse", None)
        if mouse is not None:
            mouse.record_pane(self, write_position, screen)
        xpos = write_position.xpos + write_position.width - 1
        for row, (style, char) in enumerate(scrollbar_rows(self.geometry(), getattr(self, "hub_mouse_hover", False))):
            screen.data_buffer[write_position.ypos + row][xpos] = Char(char, "class:hub " + style)


class Button(PTKButton):
    @property
    def text(self):
        return self._text() if callable(self._text) else self._text

    @text.setter
    def text(self, value):
        self._text = value

    def _get_text_fragments(self):
        provider = self._text
        if not callable(provider):
            return super()._get_text_fragments()
        # PTK reads text repeatedly to measure and center it. Resolve draft-state
        # labels only once per render, even for forms with many settings fields.
        self._text = provider()
        try:
            return super()._get_text_fragments()
        finally:
            self._text = provider

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("left_symbol", "")
        kwargs.setdefault("right_symbol", "")
        super().__init__(*args, **kwargs)

        self.control.show_cursor = False
        self.control.hub_role = "button"
        hover_style(self.window)
        registry = ShortcutRegistry()
        registry.add(["enter", " "], "Enter/Space", "activate", lambda e: self.handler and self.handler())
        registry.attach(self.control)
        self.control.key_bindings = merge_key_bindings([self.control.key_bindings, registry.bindings])


def _bind_choices(widget, *, on_move=None):
    _arrows(widget.window)
    hover_style(widget.window)
    widget.control.hub_role = "choices"
    native = widget.control.key_bindings
    registry = ShortcutRegistry()
    def invoke(event):
        keys = tuple(item.key for item in event.key_sequence)
        for binding in reversed(native.get_bindings_for_keys(keys)):
            if binding.filter():
                result = binding.handler(event)
                # Enter/Space and mouse clicks notify through _handle_enter.
                if on_move and keys[-1] in ("up", "down"):
                    on_move()
                return result
    registry.add(["up", "down"], "↑↓", "select", invoke)
    registry.add(["enter", " "], "Enter/Space", "confirm", invoke)
    registry.attach(widget.control)
    widget.control.key_bindings = merge_key_bindings([native, registry.bindings])


class RadioList(PTKRadioList):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bind_choices(self)


class CheckboxList(PTKCheckboxList):
    """Shared multi-selection control with a separate highlighted-row callback."""

    def __init__(self, *args, on_select=None, **kwargs):
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
