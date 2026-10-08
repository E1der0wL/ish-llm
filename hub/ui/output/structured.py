"""Small terminal-native renderers. Payloads describe UI, never executable code."""

import json
import math

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax

from .markdown import HubMarkdown, HubSyntaxTheme, make_console, _segment_style


class StructuredRenderer:
    def render(self, block, context):
        attrs, theme = dict(block.attributes), context.theme
        if block.kind in ("hub-code", "hub-diff"):
            visual = Syntax(block.text.strip("\n"), "diff" if block.kind == "hub-diff" else attrs.get("language", "text"),
                            theme=HubSyntaxTheme(theme), word_wrap=True)
        elif block.kind == "hub-card":
            visual = Panel(HubMarkdown(block.text, theme), title=Text(attrs.get("title", "")), border_style=theme.accent)
        else:
            data = json.loads(block.text)
            visual = Table(title=Text(attrs.get("title", "")), expand=True, border_style=theme.muted,
                           header_style=theme.accent)
            if block.kind == "hub-table":
                columns, rows = data["columns"], data["rows"]
                if not isinstance(columns, list) or not isinstance(rows, list) or not 1 <= len(columns) <= 32 or len(rows) > 500:
                    raise ValueError("Expected 1–32 columns and at most 500 rows")
                for column in columns:
                    visual.add_column(Text(str(column)))
                for row in rows:
                    if not isinstance(row, list) or len(row) != len(columns):
                        raise ValueError("Table rows must match columns")
                    visual.add_row(*(Text(str(value)) for value in row))
            else:
                items = data["values"]
                if not isinstance(items, dict) or not 1 <= len(items) <= 100:
                    raise ValueError("Expected 1–100 named chart values")
                if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in items.values()):
                    raise ValueError("Chart values must be finite nonnegative numbers")
                maximum = max(items.values()) or 1
                visual.add_column("", no_wrap=False)
                visual.add_column("", ratio=1)
                visual.add_column("", justify="right")
                visual.show_header = False
                for label, value in items.items():
                    length = round(value / maximum * max(1, min(32, context.width // 3)))
                    visual.add_row(Text(str(label)), Text("█" * length, style=theme.accent), Text(str(value)))
        console = make_console(context.width, theme)
        return [[(_segment_style(segment.style, theme.code_background), segment.text)
                 for segment in line if not segment.control]
                for line in console.render_lines(visual, console.options, pad=False)]
