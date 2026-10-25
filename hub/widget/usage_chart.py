"""Non-focusable forty-row daily token chart, painted as PTK cells."""

from hub.locales.keys import MessageKey
from ..backend.project.usage import DailyUsage, DailyUsageObservation
from ..backend.project.settings_contracts import ProjectUsageObservation
from ..config.theme_style import ThemeSelector, ThemeStyle

from datetime import date, timedelta
from math import ceil
from prompt_toolkit.layout import Window, UIContent
from prompt_toolkit.layout.controls import UIControl


class UsageChartLayout:
    HEIGHT = 40
    PLOT_HEIGHT = 34
    MIN_WIDTH = 16
    MAX_DAYS = 30
    AXIS_WIDTH = 12
    MIN_DAY_WIDTH = 2
    MIN_SIZE = 1
    LABEL_EVERY = 5
    LABEL_PHASE = 3
    LABEL_WIDTH = 8
    LABEL_FORMAT = "{value:{width}.0f}"
    EMPTY_LABEL_ROWS = 1
    BAR_GAP = 1
    LABEL_GAP = 6
    DATE_FORMAT = "%m-%d"
    AXIS_INDENT = 9
    DATE_INDENT = 10
    EMPTY = ""
    SPACE = " "
    BAR = "█"
    Y_AXIS = " │"
    CORNER = "└"
    X_AXIS = "─"
    WRAP = False
    SHOW_CURSOR = False


class DailyUsageControl(UIControl):
    def __init__(self, records, t):
        self.t = t
        self.days = {}
        for record in records:
            for raw in ProjectUsageObservation(record).daily:
                item = DailyUsageObservation(raw)
                day = item.date
                entry = self.days.setdefault(day, DailyUsage(day))
                entry.tokens += item.tokens
                entry.unknown += item.unknown

    def rows(self, width):
        if width < UsageChartLayout.MIN_WIDTH or not self.days:
            return [
                [
                    (
                        ThemeSelector.MUTED.style,
                        self.t(MessageKey.SETTINGS_USAGE_EMPTY)[:width],
                    )
                ]
            ] + [[(UsageChartLayout.EMPTY, UsageChartLayout.EMPTY)]] * (
                UsageChartLayout.HEIGHT - UsageChartLayout.EMPTY_LABEL_ROWS
            )
        end = date.fromisoformat(max(self.days))
        count = min(
            UsageChartLayout.MAX_DAYS,
            max(
                UsageChartLayout.MIN_SIZE,
                (width - UsageChartLayout.AXIS_WIDTH) // UsageChartLayout.MIN_DAY_WIDTH,
            ),
        )
        dates = [end - timedelta(days=i) for i in reversed(range(count))]
        values = [
            self.days.get(day.isoformat(), DailyUsage(day.isoformat())) for day in dates
        ]
        peak = max(UsageChartLayout.MIN_SIZE, ceil(max(v.tokens for v in values)))
        plot_height = UsageChartLayout.PLOT_HEIGHT
        cell = max(
            UsageChartLayout.MIN_SIZE, (width - UsageChartLayout.AXIS_WIDTH) // count
        )
        rows = [
            [
                (
                    ThemeStyle.joined(ThemeSelector.ACCENT.style, ThemeStyle.BOLD),
                    self.t(MessageKey.USAGE_DAILY_TITLE),
                )
            ],
            [(UsageChartLayout.EMPTY, UsageChartLayout.EMPTY)],
        ]
        for y in reversed(range(plot_height)):
            label = (
                UsageChartLayout.LABEL_FORMAT.format(
                    value=peak * (y + 1) / plot_height,
                    width=UsageChartLayout.LABEL_WIDTH,
                )
                if y % UsageChartLayout.LABEL_EVERY == UsageChartLayout.LABEL_PHASE
                or y == plot_height - 1
                else UsageChartLayout.SPACE * UsageChartLayout.LABEL_WIDTH
            )
            bars = UsageChartLayout.EMPTY.join(
                (
                    UsageChartLayout.BAR
                    if v.tokens > peak * y / plot_height
                    else UsageChartLayout.SPACE
                )
                * max(UsageChartLayout.MIN_SIZE, cell - UsageChartLayout.BAR_GAP)
                + UsageChartLayout.SPACE
                for v in values
            )
            rows.append(
                [
                    (ThemeSelector.MUTED.style, label + UsageChartLayout.Y_AXIS),
                    (ThemeSelector.ACCENT.style, bars),
                ]
            )
        rows.append(
            [
                (
                    ThemeSelector.MUTED.style,
                    UsageChartLayout.SPACE * UsageChartLayout.AXIS_INDENT
                    + UsageChartLayout.CORNER
                    + UsageChartLayout.X_AXIS * (count * cell),
                )
            ]
        )
        labels = [UsageChartLayout.SPACE] * (count * cell)
        for i in range(
            0,
            count,
            max(UsageChartLayout.MIN_SIZE, ceil(UsageChartLayout.LABEL_GAP / cell)),
        ):
            label = dates[i].strftime(UsageChartLayout.DATE_FORMAT)
            if i * cell + len(label) <= len(labels):
                labels[i * cell : i * cell + len(label)] = label
        rows.append(
            [
                (
                    ThemeSelector.MUTED.style,
                    UsageChartLayout.SPACE * UsageChartLayout.DATE_INDENT
                    + UsageChartLayout.EMPTY.join(labels),
                )
            ]
        )
        rows.append(
            [
                (
                    ThemeSelector.MUTED.style,
                    self.t(
                        MessageKey.USAGE_DAILY_RANGE,
                        start=dates[0].isoformat(),
                        end=end.isoformat(),
                    ),
                )
            ]
        )
        rows.append(
            [
                (
                    ThemeSelector.MUTED.style,
                    self.t(
                        MessageKey.USAGE_DAILY_UNKNOWN,
                        count=sum(v.unknown for v in values),
                    ),
                )
            ]
        )
        return rows

    def create_content(self, width, height):
        rows = self.rows(width)
        return UIContent(
            get_line=lambda i: rows[i],
            line_count=UsageChartLayout.HEIGHT,
            show_cursor=UsageChartLayout.SHOW_CURSOR,
        )


class DailyUsageChart(Window):
    def __init__(self, records, t):
        super().__init__(
            DailyUsageControl(records, t),
            height=UsageChartLayout.HEIGHT,
            wrap_lines=UsageChartLayout.WRAP,
        )
