"""Small terminal-native renderers. Payloads describe UI, never executable code."""

from hub.ui.output.model import OutputBlock, CodeAttributes, TitleAttributes
from dataclasses import dataclass

import json
import math

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax

from .markdown import HubMarkdown, HubSyntaxTheme, make_console, _segment_style
from .table import TableData


@dataclass(frozen=True, slots=True)
class ChartData:
    MIN_ITEMS = 1
    MAX_ITEMS = 100
    MIN_VALUE = 0
    ZERO_SCALE = 1
    INVALID_ITEMS = "Expected 1–100 named chart values"
    INVALID_VALUE = "Chart values must be finite nonnegative numbers"

    values: dict

    @classmethod
    def from_record(cls, data):
        items = data[cls.values.__name__]
        if (
            not isinstance(items, dict)
            or not cls.MIN_ITEMS <= len(items) <= cls.MAX_ITEMS
        ):
            raise ValueError(cls.INVALID_ITEMS)
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < cls.MIN_VALUE
            for value in items.values()
        ):
            raise ValueError(cls.INVALID_VALUE)
        return cls(items)

    @property
    def maximum(self):
        return max(self.values.values()) or self.ZERO_SCALE


class StructuredRenderer:
    NEWLINE = "\n"
    DIFF_LANGUAGE = "diff"
    WRAP_CODE = True
    EXPAND_TABLE = True
    EMPTY_HEADING = ""
    WRAP_LABELS = False
    BAR_RATIO = 1
    VALUE_ALIGNMENT = "right"
    SHOW_HEADER = False
    MIN_BAR_WIDTH = 1
    MAX_BAR_WIDTH = 32
    CHART_WIDTH_DIVISOR = 3
    BAR = "█"
    PAD = False

    def render(self, block, context):
        theme = context.theme
        if block.kind in (OutputBlock.HUB_CODE, OutputBlock.HUB_DIFF):
            attrs = CodeAttributes.from_pairs(block.attributes)
            visual = Syntax(
                block.text.strip(self.NEWLINE),
                (
                    self.DIFF_LANGUAGE
                    if block.kind == OutputBlock.HUB_DIFF
                    else attrs.language
                ),
                theme=HubSyntaxTheme(theme),
                word_wrap=self.WRAP_CODE,
            )
        elif block.kind == OutputBlock.HUB_CARD:
            attrs = TitleAttributes.from_pairs(block.attributes)
            visual = Panel(
                HubMarkdown(block.text, theme),
                title=Text(attrs.title),
                border_style=theme.accent,
            )
        else:
            attrs = TitleAttributes.from_pairs(block.attributes)
            data = json.loads(block.text)
            visual = Table(
                title=Text(attrs.title),
                expand=self.EXPAND_TABLE,
                border_style=theme.muted,
                header_style=theme.accent,
            )
            if block.kind == OutputBlock.HUB_TABLE:
                table = TableData.from_record(data, source=block.text)
                for column in table.columns:
                    visual.add_column(Text(column))
                for row in table.rows:
                    visual.add_row(*(Text(value) for value in row))
            else:
                chart = ChartData.from_record(data)
                items = chart.values
                maximum = chart.maximum
                visual.add_column(self.EMPTY_HEADING, no_wrap=self.WRAP_LABELS)
                visual.add_column(self.EMPTY_HEADING, ratio=self.BAR_RATIO)
                visual.add_column(self.EMPTY_HEADING, justify=self.VALUE_ALIGNMENT)
                visual.show_header = self.SHOW_HEADER
                for label, value in items.items():
                    length = round(
                        value
                        / maximum
                        * max(
                            self.MIN_BAR_WIDTH,
                            min(
                                self.MAX_BAR_WIDTH,
                                context.width // self.CHART_WIDTH_DIVISOR,
                            ),
                        )
                    )
                    visual.add_row(
                        Text(str(label)),
                        Text(self.BAR * length, style=theme.accent),
                        Text(str(value)),
                    )
        console = make_console(context.width, theme)
        return [
            [
                (_segment_style(segment.style, theme.code_background), segment.text)
                for segment in line
                if not segment.control
            ]
            for line in console.render_lines(visual, console.options, pad=self.PAD)
        ]
