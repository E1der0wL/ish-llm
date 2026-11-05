"""A fitted single row avoids prompt-toolkit's unbounded short-line cache."""
from prompt_toolkit.layout.controls import FormattedTextControl, UIContent


class ToolbarContent(UIContent):
    def get_height_for_line(self, lineno, width, get_line_prefix=None, slice_stop=None):
        # ToolbarView already fits the text to the window's full cell width.
        # The normal implementation caches every changing short line forever.
        return 1


class ToolbarControl(FormattedTextControl):
    def __init__(self, original, owned):
        self.original = original
        self._owned = owned
        super().__init__()

    def reset(self):
        super().reset()
        self.original.reset()

    def create_content(self, width, height=None):
        content = self.original.create_content(width, height)
        if not self._owned():
            return content
        return ToolbarContent(get_line=content.get_line, line_count=content.line_count,
                              cursor_position=content.cursor_position, show_cursor=False)

    def preferred_width(self, max_available_width):
        return self.original.preferred_width(max_available_width)

    def mouse_handler(self, mouse_event):
        return self.original.mouse_handler(mouse_event)
