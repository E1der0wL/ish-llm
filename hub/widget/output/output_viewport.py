"""A scrollable output object, rendered again at the available popup width."""

from prompt_toolkit.application.current import get_app
from .text_viewport import TextViewport
from ...ui.output.model import RenderContext


class OutputViewport(TextViewport):
    def __init__(self, item, view):
        super().__init__(item.raw, tail=False, render_worker=view.render_worker)
        self.item, self.view = item, view

    def render_request(self, width):
        transcript = self.view.transcript.control
        context = RenderContext(max(1, width), self.view.theme, self.view.t,
                                transcript.file_root, get_app().invalidate)
        key = (width, context.theme, context.language.code, context.root, transcript.renderers.version)
        return key, lambda: transcript.renderers.render(self.item.block, context)
