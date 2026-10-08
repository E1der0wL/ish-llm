"""Display blocks: parser boundaries, real ASCII conversion, cache, and extensibility."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image

from hub.config.theme import HubTheme
from hub.locales import Language
from hub.widget.conversation import ChatMessage, ConversationControl
from hub.ui.output import OutputParser, OutputBlock, RenderContext, RendererRegistry, ImageRenderer
from tests.hub.test_live import until


class OutputTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_preserves_code_unknown_and_malformed_blocks(self):
        parser = OutputParser()
        text = 'Hello\n\n<hub-image src="a&amp;b.png" alt="example" />\n\nAfter'
        blocks = parser.parse(text)
        self.assertEqual([b.kind for b in blocks], ["markdown", "hub-image", "markdown"])
        self.assertEqual(dict(blocks[1].attributes)["src"], "a&b.png")
        for source in ('```xml\n<hub-image src="x" />\n```', '    <hub-image src="x" />', '`<hub-image src="x" />`'):
            self.assertTrue(all(b.kind in ("markdown", "code") for b in parser.parse(source)))
        self.assertEqual(parser.parse('<hub-image src="x"')[0].kind, "literal")
        self.assertEqual(parser.parse('<hub-image src="x"', final=False)[0].kind, "pending")
        source = '<hub-test><![CDATA[<nested>]]></hub-test>'
        self.assertEqual(parser.parse(source)[0].kind, "literal")
        registry = RendererRegistry()
        context = RenderContext(80, HubTheme(), Language(), Path.cwd(), lambda: None)
        unknown = parser.parse('<hub-missing value="x" />')[0]
        self.assertIn("hub-missing", str(registry.render(unknown, context)))
        class Custom:
            def render(self, block, context):
                return [[("", dict(block.attributes)["value"])]]
        registry.register("hub-test", Custom())
        self.assertEqual(registry.render(parser.parse('<hub-test value="works" />')[0], context), [[("", "works")]])

    async def test_real_image_background_conversion_cached_width_and_error_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (40, 20), "red").save(root / "red.png")
            control = ConversationControl((ChatMessage("assistant", 'Before\n\n<hub-image src="red.png" alt="red" />\n\nAfter', id="m"),), HubTheme())
            control.file_root = root
            renderer = control.images
            ui_thread = threading.get_ident()
            threads = []
            original = renderer._convert
            def convert(*args):
                threads.append(threading.get_ident())
                return original(*args)
            try:
                with patch.object(renderer, "_convert", side_effect=convert):
                    first = control.create_content(60, 20)
                    self.assertIn("변환", "".join(t for i in range(first.line_count) for _, t in first.get_line(i)))
                    await until(lambda: renderer.revision > 0)
                    ready = control.create_content(60, 20)
                    self.assertTrue(any("fg:#" in style for i in range(ready.line_count) for style, _ in ready.get_line(i)))
                    self.assertTrue(all(thread != ui_thread for thread in threads))
                    self.assertTrue(any(key == "m:block:8" for key, _ in control._anchors))
                    control.create_content(60, 20)
                    self.assertEqual(len(threads), 1)
                    control.create_content(30, 20)
                    await until(lambda: renderer.revision > 1)
                    self.assertEqual(len(threads), 2)
                # Missing files, remote URLs and traversal remain text errors.
                for source in ("missing.png", "https://example.com/image.png", "../outside.png"):
                    block = OutputBlock("hub-image", "", 0, (("src", source),))
                    context = RenderContext(50, HubTheme(), Language(), root, lambda: None)
                    revision = renderer.revision
                    renderer.render(block, context)
                    await until(lambda: renderer.revision > revision)
                    self.assertIn("표시할 수 없습니다", str(renderer.render(block, context)))
                # Even a one-column panorama has at least one output row.
                self.assertTrue(renderer._convert(root, "red.png", None, 1))
                renderer.register_artifact("image", root / "red.png")
                block = OutputBlock("hub-image", "", 0, (("src", "artifact:image"),))
                revision = renderer.revision
                renderer.render(block, context)
                await until(lambda: renderer.revision > revision)
                self.assertIn("fg:#", str(renderer.render(block, context)))
            finally:
                control.renderers.close()
