"""Hub-local widget appearance without changing host or PTK defaults."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import ScrollablePane as PTKScrollablePane
from prompt_toolkit.layout.margins import ScrollbarMargin as PTKScrollbarMargin
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.widgets import Button as PTKButton, Checkbox as PTKCheckbox
from prompt_toolkit.widgets import RadioList as PTKRadioList, TextArea as PTKTextArea
from ..asset import icon
from ..ui.input.registry import ShortcutRegistry
from prompt_toolkit.key_binding import merge_key_bindings


def _arrows(window) -> None:
    for margin in (*window.left_margins, *window.right_margins):
        if isinstance(margin, PTKScrollbarMargin):
            margin.up_arrow_symbol, margin.down_arrow_symbol = icon.SCROLL_UP, icon.SCROLL_DOWN


class ScrollbarMargin(PTKScrollbarMargin):
    def __init__(self, **kwargs):
        super().__init__(up_arrow_symbol=icon.SCROLL_UP, down_arrow_symbol=icon.SCROLL_DOWN, **kwargs)

    def create_margin(self, window_render_info, width, height):
        if not width or height < 1:
            return []
        arrows = self.display_arrows() and height >= 3
        track = height - 2 if arrows else height
        total = max(1, window_render_info.content_height)
        visible = min(total, window_render_info.window_height)
        thumb = min(track, max(1, round(track * visible / total)))
        remaining = max(0, total - visible)
        top = round((track - thumb) * min(remaining, window_render_info.vertical_scroll) / remaining) if remaining else 0
        rows = [("class:hub.scrollbar-arrow", icon.SCROLL_UP)] if arrows else []
        rows.extend(("class:hub.scrollbar-thumb", icon.SCROLL_THUMB) if top <= row < top + thumb
                    else ("class:hub.scrollbar-track", icon.SCROLL_TRACK) for row in range(track))
        if arrows:
            rows.append(("class:hub.scrollbar-arrow", icon.SCROLL_DOWN))
        return [fragment for i, row in enumerate(rows)
                for fragment in ([row, ("", "\n")] if i < len(rows) - 1 else [row])]


class ScrollablePane(PTKScrollablePane):
    def __init__(self, content, **kwargs):
        super().__init__(content, up_arrow_symbol=icon.SCROLL_UP, down_arrow_symbol=icon.SCROLL_DOWN, **kwargs)

    def _draw_scrollbar(self, write_position, content_height, screen) -> None:
        super()._draw_scrollbar(write_position, content_height, screen)
        # PTK draws these cells directly, without the parent container's style.
        xpos = write_position.xpos + write_position.width - 1
        for ypos in range(write_position.ypos, write_position.ypos + write_position.height):
            cell = screen.data_buffer[ypos][xpos]
            if "class:scrollbar." in cell.style:
                screen.data_buffer[ypos][xpos] = Char(cell.char, "class:hub " + cell.style)


class Button(PTKButton):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("left_symbol", "")
        kwargs.setdefault("right_symbol", "")
        super().__init__(*args, **kwargs)

        self.control.show_cursor = False
        self.control.hub_role = "button"
        registry = ShortcutRegistry()
        registry.add(["enter", " "], "Enter/Space", "activate", lambda e: self.handler and self.handler())
        self.control.hub_shortcuts = registry
        self.control.key_bindings = merge_key_bindings([self.control.key_bindings, registry.bindings])


class Checkbox(PTKCheckbox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_scrollbar = False
        # A single-item list always has a selected row; use actual keyboard focus.
        self.selected_style = ""
        self.window.style = lambda: ("class:checkbox-list.focused" if get_app().layout.has_focus(self)
                                     else "class:checkbox-list")


class RadioList(PTKRadioList):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _arrows(self.window)
        self.control.hub_role = "choices"
        native = self.control.key_bindings
        registry = ShortcutRegistry()
        def invoke(event):
            bindings = native.get_bindings_for_keys(tuple(item.key for item in event.key_sequence))
            for binding in reversed(bindings):
                if binding.filter():
                    return binding.handler(event)
        registry.add(["up", "down"], "↑↓", "select", invoke)
        registry.add(["enter", " "], "Enter/Space", "confirm", invoke)
        self.control.hub_shortcuts = registry
        self.control.key_bindings = merge_key_bindings([native, registry.bindings])


class TextArea(PTKTextArea):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _arrows(self.window)
