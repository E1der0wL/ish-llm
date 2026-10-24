"""One-line, cell-width-aware shortcut legend shared by every page."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from ..config.theme_style import ThemeSelector


class ShortcutBar(Window):
    HEIGHT = 1
    STYLE = ""
    PADDING = " "
    PADDING_WIDTH = get_cwidth(PADDING)
    ITEM_SEPARATOR = " · "
    FIRST_ITEM_PREFIX = ""
    ELLIPSIS = " …"
    ELLIPSIS_WIDTH = get_cwidth(ELLIPSIS)

    def __init__(self, hints, width):
        self.hints, self.width_available = hints, width
        super().__init__(
            FormattedTextControl(self.fragments),
            height=self.HEIGHT,
            style=ThemeSelector.FOOTER.style,
        )

    def fragments(self):
        result, used = [(self.STYLE, self.PADDING)], self.PADDING_WIDTH
        items = self.hints()
        for index, (key, label) in enumerate(items):
            text = (
                (self.ITEM_SEPARATOR if index else self.FIRST_ITEM_PREFIX)
                + key
                + self.PADDING
                + label
            )
            reserve = self.ELLIPSIS_WIDTH if index < len(items) - 1 else 0
            if used + get_cwidth(text) + reserve > self.width_available():
                if used + self.ELLIPSIS_WIDTH <= self.width_available():
                    result.append((self.STYLE, self.ELLIPSIS))
                break
            result.append((self.STYLE, text))
            used += get_cwidth(text)
        return result
