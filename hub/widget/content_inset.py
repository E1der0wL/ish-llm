"""Shared right/bottom spacing and optional fixed heading for page content."""

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension


class ContentInset(VSplit):
    def __init__(self, content, *, heading=None, **kwargs):
        # Preserve the body and controls on short terminals; spacing yields
        # before a required row disappears.
        padding_height = Dimension(min=0, preferred=1, max=1)
        rows = []
        if heading is not None:
            rows.append(Window(FormattedTextControl(heading), height=1, style="class:hub.title"))
        rows.extend([content, Window(height=padding_height)])
        super().__init__([HSplit(rows), Window(width=1)], **kwargs)
