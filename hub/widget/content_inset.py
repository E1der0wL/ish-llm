"""Align unframed page content with the inside of the footer's frame."""

from prompt_toolkit.layout import VSplit, Window


class ContentInset(VSplit):
    def __init__(self, content, **kwargs):
        super().__init__([Window(width=1), content], **kwargs)
