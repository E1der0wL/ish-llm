"""Reserve a one-cell horizontal inset for unframed page content."""

from prompt_toolkit.layout import VSplit, Window


class ContentInset(VSplit):
    def __init__(self, content, *, right=False, **kwargs):
        padding = Window(width=1)
        super().__init__([content, padding] if right else [padding, content], **kwargs)
