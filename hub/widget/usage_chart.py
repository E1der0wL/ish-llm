"""Non-focusable forty-row daily token chart, painted as PTK cells."""

from hub.locales.keys import MessageKey

from datetime import date, timedelta
from math import ceil
from prompt_toolkit.layout import Window, UIContent
from prompt_toolkit.layout.controls import UIControl


class DailyUsageControl(UIControl):
    def __init__(self, records, t):
        self.t = t
        self.days = {}
        for record in records:
            for item in record.get("daily", ()):
                entry = self.days.setdefault(item["date"], [0, 0])
                entry[0] += item["tokens"]
                entry[1] += item["unknown"]

    def rows(self, width):
        if width < 16 or not self.days:
            return [[("class:hub.muted", self.t(MessageKey.SETTINGS_USAGE_EMPTY)[:width])]] + [[("", "")]] * 39
        end = date.fromisoformat(max(self.days))
        count = min(30, max(1, (width - 12) // 2))
        dates = [end - timedelta(days=i) for i in reversed(range(count))]
        values = [self.days.get(day.isoformat(), [0, 0]) for day in dates]
        peak = max(1, ceil(max(v[0] for v in values)))
        plot_height = 34
        cell = max(1, (width - 12) // count)
        rows = [[("class:hub.accent bold", self.t(MessageKey.USAGE_DAILY_TITLE))], [("", "")]]
        for y in reversed(range(plot_height)):
            label = f"{peak * (y + 1) / plot_height:8.0f}" if y % 5 == 3 or y == plot_height - 1 else " " * 8
            bars = "".join(("█" if v[0] > peak * y / plot_height else " ") * max(1, cell - 1) + " " for v in values)
            rows.append([("class:hub.muted", label + " │"), ("class:hub.accent", bars)])
        rows.append([("class:hub.muted", " " * 9 + "└" + "─" * (count * cell))])
        labels = [" "] * (count * cell)
        for i in range(0, count, max(1, ceil(6 / cell))):
            label = dates[i].strftime("%m-%d")
            if i * cell + len(label) <= len(labels):
                labels[i * cell:i * cell + len(label)] = label
        rows.append([("class:hub.muted", " " * 10 + "".join(labels))])
        rows.append([("class:hub.muted", self.t(MessageKey.USAGE_DAILY_RANGE, start=dates[0].isoformat(), end=end.isoformat()))])
        rows.append([("class:hub.muted", self.t(MessageKey.USAGE_DAILY_UNKNOWN, count=sum(v[1] for v in values)))])
        return rows

    def create_content(self, width, height):
        rows = self.rows(width)
        return UIContent(get_line=lambda i: rows[i], line_count=40, show_cursor=False)


class DailyUsageChart(Window):
    def __init__(self, records, t):
        super().__init__(DailyUsageControl(records, t), height=40, wrap_lines=False)
