"""Dedicated code viewer: syntax display, search, source and clean-code copy."""

from hub.locales.keys import MessageKey

from hub.ui.output.model import OutputBlock

import asyncio
from markdown_it import MarkdownIt
from prompt_toolkit.application.current import get_app

from ..clipboard import copy_text
from ..controls import Button
from ..output_inspector import OutputInspector


class CodeViewer(OutputInspector):
    def __init__(self, item, context):
        super().__init__(item, context)
        self.actions.insert(-1, Button(self.t(MessageKey.OBJECT_COPY_CODE),
            handler=lambda: get_app().create_background_task(self.copy_code())))

    def code_text(self):
        if self.item.block.kind == OutputBlock.HUB_CODE:
            return self.item.block.text
        tokens = MarkdownIt("commonmark").parse(self.item.raw)
        return "".join(token.content for token in tokens if token.type in ("fence", "code_block"))

    async def copy_code(self):
        await copy_text(await asyncio.to_thread(self.code_text))
        get_app().invalidate()
