"""Reserve the shared one-cell right inset for unframed page content."""

from prompt_toolkit.layout import VSplit, Window


class ContentInset(VSplit):
    def __init__(self, content, **kwargs):
        super().__init__([content, Window(width=1)], **kwargs)
