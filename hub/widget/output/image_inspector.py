"""Expanded image presentation: explicit ASCII/native/auto, no network fetch."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from hub.locales.keys import MessageKey
from hub.ui.output.graphics import KittyGraphics
from hub.ui.output.worker import RenderSlot
from hub.widget.controls import Button
from hub.widget.output_inspector import ExpandedOutputDialog
from enum import StrEnum


class ImageMode(StrEnum):
    ASCII = 'ascii'
    NATIVE = 'native'
    AUTO = 'auto'


class ImageInspector(ExpandedOutputDialog):
    def __init__(self, item, context, renderer, root):
        self.renderer, self.root = renderer, root()
        self.native, self.painter, self.closed, self.generation = False, None, False, 0
        self.canvas = Window(FormattedTextControl(''))
        output = context.viewport(item)
        super().__init__(item, context, output,
                         body=DynamicContainer(lambda: self.canvas if self.native else output))
        for mode, label in ((ImageMode.ASCII, MessageKey.IMAGE_ASCII), (ImageMode.NATIVE, MessageKey.IMAGE_NATIVE),
                            (ImageMode.AUTO, MessageKey.IMAGE_AUTO)):
            self.actions.insert(0, Button(self.t(label), handler=lambda mode=mode: self.choose(mode)))

    def choose(self, mode):
        if self.closed:
            return
        mode = ImageMode(mode)
        self.generation += 1
        generation = self.generation
        self.context.render_worker.cancel(RenderSlot.POPUP)
        if self.painter is not None:
            self.painter.close()
            self.painter = None
        supported = KittyGraphics.supported()
        self.native = mode != ImageMode.ASCII and supported
        if not self.native:
            self.notice = self.t(MessageKey.IMAGE_UNSUPPORTED) if mode == ImageMode.NATIVE else ''
            get_app().invalidate()
            return
        self.notice = self.t(MessageKey.SETTINGS_LOADING)
        root, number = self.root, KittyGraphics.create_number()
        def build():
            data = self.renderer.read_png(self.item.block, root)
            return KittyGraphics.encode(data, number), None
        def ready(value):
            if self.closed or self.generation != generation:
                return
            payload, error = value
            if error is not None:
                self.native = False
                self.notice = self.t(MessageKey.OUTPUT_ERROR, error=error)
            else:
                self.notice = ''
                self.painter = KittyGraphics(get_app(), self.canvas, payload, number)
            get_app().invalidate()
        self.context.render_worker.request(RenderSlot.POPUP, build, ready, lambda error: (None, str(error)))
        get_app().invalidate()

    def suspend(self, app):
        self.generation += 1
        self.context.render_worker.cancel(RenderSlot.POPUP)
        if self.painter is not None:
            self.painter.close()
            self.painter = None

    def resume(self, app):
        if self.closed or not self.native:
            return
        # Revalidate the original source after returning to the popup. Never
        # resolve an old block against a newly selected Project's working root.
        self.choose(ImageMode.NATIVE)

    def dispose(self):
        self.closed = True
        self.generation += 1
        if self.painter is not None:
            self.painter.close()
            self.painter = None
