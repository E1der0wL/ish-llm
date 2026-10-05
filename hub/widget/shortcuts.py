"""One-line, cell-width-aware shortcut legend shared by every page."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth


class ShortcutBar(Window):
    def __init__(self, hints, width):
        self.hints, self.width_available = hints, width
        super().__init__(FormattedTextControl(self.fragments), height=1, style="class:hub.footer")

    def fragments(self):
        result, used = [("", " ")], 1
        items = self.hints()
        for index, (key, label) in enumerate(items):
            text = (" · " if index else "") + key + " " + label
            reserve = 2 if index < len(items) - 1 else 0
            if used + get_cwidth(text) + reserve > self.width_available():
                if used + 2 <= self.width_available():
                    result.append(("", " …"))
                break
            result.append(("", text))
            used += get_cwidth(text)
        return result
