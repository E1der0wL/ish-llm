"""One-line, cell-width-aware shortcut legend shared by every page."""

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.utils import get_cwidth
from ..config.theme_style import ThemeSelector
from ..asset.icon import IconGlyphs


class ShortcutBar(Window):
    CHEVRON = IconGlyphs.SHORTCUT_CHEVRON
    MODIFIERS = ("Ctrl", "Alt")
    HEIGHT = 1
    STYLE = ""
    ELLIPSIS = " …"
    ELLIPSIS_WIDTH = get_cwidth(ELLIPSIS)

    def __init__(self, hints, width):
        self.hints, self.width_available = hints, width
        super().__init__(
            FormattedTextControl(self.fragments),
            height=self.HEIGHT,
            style=ThemeSelector.FOOTER.style,
        )

    @classmethod
    def key_parts(cls, key):
        for modifier in cls.MODIFIERS:
            if key.startswith(modifier + "+") or key.startswith(modifier + " +"):
                return modifier, key[len(modifier):].lstrip(" +")
        return "", key

    @staticmethod
    def cells(fragments):
        return sum(get_cwidth(text) for _, text in fragments)

    def group(self, modifier, items, width):
        if not items:
            return []
        result = [(self.STYLE, modifier + " + ")]
        used = self.cells(result)
        for index, (key, label) in enumerate(items):
            fragments = [
                (ThemeSelector.SHORTCUT_LEADING.style, self.CHEVRON),
                (ThemeSelector.SHORTCUT_SEGMENT.style, " <"),
                (ThemeSelector.SHORTCUT_KEY.style, key),
                (ThemeSelector.SHORTCUT_SEGMENT.style, "> " + label + " "),
                (ThemeSelector.SHORTCUT_EDGE.style, self.CHEVRON),
            ]
            size = self.cells(fragments)
            reserve = self.ELLIPSIS_WIDTH if index < len(items) - 1 else 0
            if used + size + reserve > width:
                if used + self.ELLIPSIS_WIDTH <= width:
                    result.append((self.STYLE, self.ELLIPSIS))
                return result if self.cells(result) <= width else []
            result.extend(fragments)
            used += size
        return result

    def fragments(self):
        groups = {modifier: [] for modifier in self.MODIFIERS}
        for key, label in self.hints():
            modifier, key = self.key_parts(key)
            if modifier in groups:
                groups[modifier].append((key, label))
        width = max(0, self.width_available())
        left = self.group(self.MODIFIERS[0], groups[self.MODIFIERS[0]], width)
        right = self.group(self.MODIFIERS[1], groups[self.MODIFIERS[1]], width)
        if left and right and self.cells(left) + self.cells(right) >= width:
            left_budget = min(self.cells(left), max(width // 2, width - self.cells(right) - 1))
            left = self.group(self.MODIFIERS[0], groups[self.MODIFIERS[0]], left_budget)
            right = self.group(self.MODIFIERS[1], groups[self.MODIFIERS[1]],
                               max(0, width - self.cells(left) - 1))
        gap = max(0, width - self.cells(left) - self.cells(right))
        return [*left, (self.STYLE, " " * gap), *right]
