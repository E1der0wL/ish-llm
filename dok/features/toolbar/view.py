"""Cell-aware single-row layout, graphs and right-edge system metrics."""
from dataclasses import dataclass

from ...rendering.text import fit, width
from ...rendering.presentation import TextRow, TextSpan
from ...core.theme import Theme
from .config import Metric, RegionLayout, ToolbarConfig
from .editor import NumberCalculator
from ...locales import Locale


class MetricFormat:
    UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    CPU_BLOCKS = "▁▂▃▄▅▆▇█"
    # Braille dot rows, bottom to top, for each half of a terminal cell.
    DOTS = ((0x40, 0x04, 0x02, 0x01), (0x80, 0x20, 0x10, 0x08))

    @classmethod
    def bytes(cls, value, *, rate=False, compact=False, places=1):
        if value is None:
            return "—"
        for unit in cls.UNITS:
            if value < 1024 or unit == cls.UNITS[-1]:
                text = f"{value:.{places}f}".removesuffix(".0")
                if compact:
                    return text + unit[0] + ("/s" if rate else "")
                return text + " " + unit + ("/s" if rate else "")
            value /= 1024

    @staticmethod
    def percent(value):
        return f"{value:.0f}%" if value is not None else "—"

    @classmethod
    def cpu_history(cls, values, columns):
        """One sample per cell; only unmeasured leading history gets a baseline."""
        if columns <= 0:
            return ""
        samples = (None,) * max(0, columns - len(values)) + tuple(values[-columns:])
        result = []
        started = False
        for value in samples:
            if value is None:
                result.append(" " if started else cls.CPU_BLOCKS[0])
            else:
                started = True
                level = int(max(0, min(100, value)) * len(cls.CPU_BLOCKS) / 100)
                result.append(cls.CPU_BLOCKS[min(level, len(cls.CPU_BLOCKS) - 1)])
        return "".join(result)

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
    def label(cls, kind, snapshot, config, separator=" "):
        if kind == Metric.CPU:
            return cls.percent(snapshot.cpu_percent)
        if kind == Metric.MEMORY:
            return cls.percent(snapshot.memory_percent)
        def rates(*, compact=False, places=1):
            received = cls.bytes(snapshot.received_per_second, rate=not compact, compact=compact, places=places)
            sent = cls.bytes(snapshot.sent_per_second, rate=True, compact=compact, places=places)
            return f"{config.download_label}{received}{separator}{config.upload_label}{sent}"

        return rates(), rates(compact=True), rates(compact=True, places=0)

    @classmethod
    def graph(cls, kind, snapshot, history, columns):
        if kind == Metric.CPU:
            return cls.cpu_history(tuple(item.cpu_percent for item in history), columns)
        if kind == Metric.MEMORY:
            return cls.bar(snapshot.memory_percent, columns)
        values = tuple(None if item.received_per_second is None or item.sent_per_second is None
                       else item.received_per_second + item.sent_per_second for item in history)
        return cls.line(values, columns, scale=max((value for value in values if value is not None), default=1))


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    color: str


@dataclass(slots=True)
class Region:
    layout: RegionLayout
    parts: list[Segment]
    separator: str

    def text_width(self) -> int:
        return sum(width(part.text) for part in self.parts) + max(0, len(self.parts) - 1) * width(self.separator)

    def size(self, following: str) -> int:
        content = self.layout.width or self.text_width()
        return self.layout.pad_left + content + self.layout.after_width(following)

    def paint(self, available: int, following: str, background: str | None, theme: Theme):
        layout = self.layout
        after = layout.after(following, available)
        cells = min(layout.content_room(available, after), layout.width or self.text_width())
        spans = []
        neutral = layout.style.paint(None, background, theme)
        for index, part in enumerate(self.parts):
            if index and self.separator:
                spans.append(TextSpan(self.separator, ""))
            spans.append(TextSpan(part.text, part.color))
        row = TextRow(tuple(spans)).fitted(cells)
        before = " " * min(layout.pad_left, available)
        fragments = [(neutral, before)] + [(layout.style.paint(span.color or None, background, theme), span.text) for span in row.spans]
        fragments.append((neutral, " " * (cells - row.cells)))
        fragments.append((neutral, fit(after, available - len(before) - cells, marker="")))
        return fragments


class ToolbarView:
    def __init__(self):
        self._numbers = NumberCalculator()

    @staticmethod
    def _visible(regions, separator):
        active = [region for region in regions if region.parts]
        return [(region, separator if index + 1 < len(active) else "") for index, region in enumerate(active)]

    @classmethod
    def _size(cls, regions, separator):
        return sum(region.size(following) for region, following in cls._visible(regions, separator))

    @classmethod
    def _paint(cls, regions, separator, cells, background, theme):
        fragments = []
        remaining = cells
        for region, following in cls._visible(regions, separator):
            fragments.extend(region.paint(remaining, following, background, theme))
            remaining = max(0, remaining - region.size(following))
        return fragments, cells - remaining

    def _left(self, config, editor, now, dock_visible, budget, theme, locale):
        if budget <= 0:
            return [], 0
        layouts = config.regions
        date = Region(layouts.date, [], "")
        if config.date.visible(dock_visible):
            text = config.date.render(now, locale)
            if text:
                date.parts.append(Segment(text, config.date.style.color))
        inputs = []
        for layout, items in zip((layouts.size, layouts.cursor), config.input.groups()):
            room = layout.content_room(budget, layout.after("", budget))
            parts = [Segment(item.render(editor, room, layout, locale=locale), item.style.color) for item in items[:max(1, room)]]
            inputs.append(Region(layout, parts, layout.between(config.separator, room)))
        numbers = Region(layouts.numbers, [], "")
        if config.numbers.enabled:
            summary = self._numbers.calculate(editor, config.numbers.scope)
            room = layouts.numbers.content_room(budget, layouts.numbers.after("", budget))
            text = config.numbers.render(summary, room, layouts.numbers.gap, layouts.numbers, locale=locale)
            if text:
                numbers.parts.append(Segment(text, config.numbers.style.color))
        regions = [date, *inputs, numbers]
        gap = " " * min(config.gap, budget)
        if self._size(regions, gap) > budget and any(region.parts for region in regions[1:]):
            date.parts.clear()
        count = sum(len(region.parts) for region in regions)
        for region in reversed(inputs):
            while region.parts and count > 1 and self._size(regions, gap) > budget:
                region.parts.pop()
                count -= 1
        return self._paint(regions, gap, budget, config.background, theme)

    def render(self, config: ToolbarConfig, editor, snapshot, history, now, dock_visible, columns, *, theme: Theme = Theme(), locale=Locale()):
        if columns <= 0:
            return []
        padding_left, columns, padding_right = config.split_width(columns)
        background = theme.paint(background=config.background)
        if not columns:
            return [(background, " " * (padding_left + padding_right))]
        entries = list(config.system.items())
        right = []
        for kind, item in entries:
            layout = config.regions.metric(kind)
            room = layout.content_room(columns, layout.after("", columns))
            gap = layout.between(" ", room)
            value = MetricFormat.label(kind, snapshot, config.system, gap)
            caption = item.caption(value, room, gap)
            right.append(Region(layout, [Segment(caption, item.style.color)], gap))
        while len(right) > 1 and self._size(right, config.separator) > columns:
            right.pop()
            entries.pop()
        base = min(columns, self._size(right, config.separator))
        left, left_width = self._left(config, editor, now, dock_visible, columns - base - bool(right), theme, locale)
        spare = max(0, columns - base - left_width - bool(left and right))

        # Fixed-width regions already own their graph space. Auto-sized graphs
        # share spare cells after both the right values and left fields fit.
        lengths = [0] * len(right)
        automatic = []
        for i, (region, following) in enumerate(self._visible(right, config.separator)):
            graph = entries[i][1].graph
            if graph.enabled:
                if region.layout.width is None:
                    automatic.append(i)
                else:
                    room = region.layout.content_room(columns, region.layout.after(following, columns))
                    lengths[i] = min(graph.width, max(0, room - region.text_width() - width(region.separator)))
        while automatic and spare:
            pending = []
            for i in automatic:
                cost = 1 if lengths[i] else 1 + width(right[i].separator)
                if cost <= spare:
                    lengths[i] += 1
                    spare -= cost
                    if lengths[i] < entries[i][1].graph.width:
                        pending.append(i)
            automatic = pending
        for region, (kind, item), length in zip(right, entries, lengths):
            if length:
                region.parts.append(Segment(MetricFormat.graph(kind, snapshot, history, length), item.style.color))
        fragments, right_width = self._paint(right, config.separator, columns, config.background, theme)
        middle = (background, " " * (columns - left_width - right_width))
        return [(background, " " * padding_left), *left, middle, *fragments,
                (background, " " * padding_right)]
