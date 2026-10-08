"""A scrollable output object, rendered again at the available popup width."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.utils import get_cwidth

from .text_viewport import TextViewport
from ..ui.output.model import RenderContext


class OutputViewport(TextViewport):
    def __init__(self, item, view):
        super().__init__(item.raw, tail=False)
        self.item, self.view = item, view

    def create_content(self, width, height):
        transcript = self.view.transcript.control
        context = RenderContext(max(1, width), self.view.theme, self.view.t,
                                transcript.file_root, get_app().invalidate)
        key = (width, context.theme, context.language.code, context.root, transcript.renderers.version)
        if key != self._render_key:
            self.fragments = transcript.renderers.render(self.item.block, context) or [[("", "")]]
            self.lines = ["".join(text for _, text in row) for row in self.fragments]
            self._max_width = max(map(get_cwidth, self.lines))
            self._render_key = key
        return super().create_content(width, height)
