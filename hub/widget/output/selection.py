"""Rendered-text selection independent of a control's keyboard focus."""

from copy import copy

from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import UIControl


class TextSelection:
    def __init__(self):
        self.anchor = self.end = None
        self.content = None
        self.width = None

    def clear(self):
        self.anchor = self.end = None

    def point(self, point):
        row = max(0, min(self.content.line_count - 1, point.y))
        return Point(max(0, min(len(self.line(row)), point.x)), row)

    def begin(self, point):
        self.anchor = self.end = self.point(point)

    def extend(self, point):
        self.end = self.point(point)

    def line(self, row):
        return "".join(fragment[1] for fragment in self.content.get_line(row)
                       if "[ZeroWidthEscape]" not in fragment[0])

    @property
    def bounds(self):
        if self.anchor is None or self.anchor == self.end:
            return None
        return sorted((self.point(self.anchor), self.point(self.end)), key=lambda p: (p.y, p.x))

    @property
    def text(self):
        bounds = self.bounds
        if bounds is None:
            return ""
        start, end = bounds
        if start.y == end.y:
            return self.line(start.y)[start.x:end.x]
        return "\n".join([self.line(start.y)[start.x:].rstrip(),
                          *(self.line(row).rstrip() for row in range(start.y + 1, end.y)),
                          self.line(end.y)[:end.x]])

    def apply(self, content, width):
        if self.width != width:
            self.clear()  # A reflow changes rendered row coordinates.
        self.width, self.content = width, content
        result = copy(content)

        def selected(row):
            fragments = content.get_line(row)
            bounds = self.bounds
            if bounds is None or not bounds[0].y <= row <= bounds[1].y:
                return fragments
            start, end = bounds
            left = start.x if row == start.y else 0
            right = end.x if row == end.y else len(self.line(row))
            pieces, offset = [], 0
            for fragment in fragments:
                style, text, *extra = fragment
                if "[ZeroWidthEscape]" in style:
                    pieces.append(fragment)
                    continue
                a, b = max(0, left - offset), min(len(text), right - offset)
                if a < b:
                    pieces.extend((style + suffix, part, *extra) for suffix, part in (
                        ("", text[:a]), (" class:hub.text-selection", text[a:b]), ("", text[b:])) if part)
                else:
                    pieces.append(fragment)
                offset += len(text)
            return pieces

        result.get_line = selected
        return result


class SelectableControl(UIControl):
    """Opt-in selection only; is_focusable remains UIControl's False default."""

    def __init__(self):
        super().__init__()
        self.selection = TextSelection()

    def select_content(self, content, width):
        return self.selection.apply(content, width)
