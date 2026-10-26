"""Shared right/bottom spacing and optional fixed heading for page content."""

from ..config.theme_style import ThemeSelector

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension


class ContentInset(VSplit):
    NO_HEADING = None
    MINIMUM_PADDING = 0
    PADDING = 1
    HEADING_HEIGHT = 1

    def __init__(self, content, *, heading=NO_HEADING, bottom_padding=PADDING, **kwargs):
        # Preserve the body and controls on short terminals; spacing yields
        # before a required row disappears.
        padding_height = Dimension(
            min=self.MINIMUM_PADDING, preferred=bottom_padding, max=bottom_padding
        )
        rows = []
        if heading is not self.NO_HEADING:
            rows.append(
                Window(
                    FormattedTextControl(heading),
                    height=self.HEADING_HEIGHT,
                    style=ThemeSelector.TITLE.style,
                )
            )
        rows.extend([content, Window(height=padding_height)])
        super().__init__([HSplit(rows), Window(width=self.PADDING)], **kwargs)
