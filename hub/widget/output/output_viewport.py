"""A scrollable output object, rendered again at the available popup width."""

from prompt_toolkit.application.current import get_app
from .text_viewport import TextViewport, ViewportRenderKey
from ...ui.output.model import RenderContext


class OutputViewport(TextViewport):
    FOLLOW_TAIL = False

    def __init__(self, item, output):
        super().__init__(
            item.raw, tail=self.FOLLOW_TAIL, render_worker=output.view.render_worker
        )
        self.item, self.output = item, output

    def render_request(self, width):
        transcript = self.output.transcript.control
        view = self.output.view
        context = RenderContext(
            max(self.MIN_DIMENSION, width),
            view.theme,
            view.t,
            transcript.file_root,
            get_app().invalidate,
        )
        key = ViewportRenderKey.create(
            width,
            context.theme,
            context.language.code,
            context.root,
            transcript.renderers.version,
        )
        return key, lambda: transcript.renderers.render(self.item.block, context)
