"""Cell-aware single-row layout, graphs and right-edge system metrics."""
from dataclasses import dataclass

from ...rendering.text import fit, width
from .config import Metric, ToolbarConfig
from .editor import NumberCalculator


class MetricFormat:
    UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    # Braille dot rows, bottom to top, for each half of a terminal cell.
    DOTS = ((0x40, 0x04, 0x02, 0x01), (0x80, 0x20, 0x10, 0x08))

    @classmethod
    def bytes(cls, value, *, rate=False):
        if value is None:
            return "—"
        for unit in cls.UNITS:
            if value < 1024 or unit == cls.UNITS[-1]:
                text = f"{value:.1f}".rstrip("0").rstrip(".")
                return text + " " + unit + ("/s" if rate else "")
            value /= 1024

    @staticmethod
    def percent(value):
        return f"{value:.0f}%" if value is not None else "—"

    @classmethod
    def line(cls, values, columns, *, scale=100):
        if columns <= 0:
            return ""
        values = (None,) * max(0, columns * 2 - len(values)) + tuple(values[-columns * 2:])
        result = []
        for index in range(0, len(values), 2):
            bits = 0
            for half in (0, 1):
                value = values[index + half]
                if value is not None:
                    row = round(max(0, min(1, value / max(scale, 1))) * 3)
                    bits |= cls.DOTS[half][row]
            result.append(chr(0x2800 + bits))
        return "".join(result)

    @staticmethod
    def bar(percent, columns):
        filled = round(columns * percent / 100) if percent is not None else 0
        return "━" * filled + "─" * (columns - filled)

    @classmethod
    def label(cls, kind, snapshot, config):
        if kind == Metric.CPU:
            return cls.percent(snapshot.cpu_percent)
        if kind == Metric.MEMORY:
            return f"{cls.bytes(snapshot.memory_used)}/{cls.bytes(snapshot.memory_total)} {cls.percent(snapshot.memory_percent)}"
        return f"{config.download_label}{cls.bytes(snapshot.received_per_second, rate=True)} {config.upload_label}{cls.bytes(snapshot.sent_per_second, rate=True)}"

    @classmethod
    def graph(cls, kind, snapshot, history, columns):
        if kind == Metric.CPU:
            return cls.line(tuple(item.cpu_percent for item in history), columns)
        if kind == Metric.MEMORY:
            return cls.bar(snapshot.memory_percent, columns)
        values = tuple(None if item.received_per_second is None or item.sent_per_second is None
                       else item.received_per_second + item.sent_per_second for item in history)
        return cls.line(values, columns, scale=max((value for value in values if value is not None), default=1))


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    color: str


class ToolbarView:
    BASE_STYLE = "noreverse bg:default"

    def __init__(self):
        self._numbers = NumberCalculator()

    @staticmethod
    def _size(parts, separator):
        return sum(width(part.text) for part in parts) + max(0, len(parts) - 1) * width(separator)

    @classmethod
    def _fragments(cls, parts, separator):
        fragments = []
        for index, part in enumerate(parts):
            if index:
                fragments.append((cls.BASE_STYLE, separator))
            fragments.append((f"{cls.BASE_STYLE} {part.color}", part.text))
        return fragments

    def render(self, config: ToolbarConfig, editor, snapshot, history, now, dock_visible, columns):
        if columns <= 0:
            return []
        separator = config.separator
        left = []
        date_text = config.date.render(now) if config.date.visible(dock_visible) else ""
        date_visible = bool(date_text)
        if date_visible:
            left.append(Segment(date_text, config.date.style.color))
        if config.input.enabled:
            left.extend(Segment(item.label + editor.value(item.field), item.style.color)
                        for item in config.input.items if item.enabled)
        numbers_text = ""
        if config.numbers.enabled:
            summary = self._numbers.calculate(editor, config.numbers.scope)
            numbers_text = config.numbers.render(summary)
        has_numbers = bool(numbers_text)
        if has_numbers:
            left.append(Segment(numbers_text, config.numbers.style.color))
        entries = list(config.system.items())
        right = [Segment((item.label + " " if item.label else "") + MetricFormat.label(kind, snapshot, config.system), item.style.color)
                 for kind, item in entries]
        # Protect right-side values before allocating any graph columns.
        while len(right) > 1 and self._size(right, separator) > columns:
            right.pop()
            entries.pop()
        if right and self._size(right, separator) > columns:
            right[0] = Segment(fit(right[0].text, columns), right[0].color)
        budget = max(0, columns - self._size(right, separator) - (1 if right and left else 0))
        # Date is optional. Later input fields go next; preserve the number
        # summary as long as it fits. The final remaining field is clipped.
        if date_visible and self._size(left, separator) > budget:
            left.pop(0)
        while len(left) > 1 and self._size(left, separator) > budget:
            left.pop(-2 if has_numbers else -1)
        if left and self._size(left, separator) > budget:
            left[0] = Segment(fit(left[0].text, budget), left[0].color)
        left = [part for part in left if part.text]
        spare = max(0, columns - self._size(left, separator) - self._size(right, separator)
                    - (1 if left and right else 0))
        lengths = [0] * len(entries)
        # Allocate graph cells evenly. A graph also needs its leading space.
        while spare:
            changed = False
            for index, (_, item) in enumerate(entries):
                cost = 2 if lengths[index] == 0 else 1
                if item.graph.enabled and lengths[index] < item.graph.columns and spare >= cost:
                    lengths[index] += 1
                    spare -= cost
                    changed = True
            if not changed:
                break
        for index, ((kind, _), count) in enumerate(zip(entries, lengths)):
            if count:
                right[index] = Segment(right[index].text + " " + MetricFormat.graph(kind, snapshot, history, count), right[index].color)
        gap = max(0, columns - self._size(left, separator) - self._size(right, separator))
        return (self._fragments(left, separator) + [(self.BASE_STYLE, " " * gap)]
                + self._fragments(right, separator))
