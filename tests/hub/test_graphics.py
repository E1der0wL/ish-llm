"""Terminal graphics cannot paint outside its Hub window or delete foreign images."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from prompt_toolkit.utils import Event

from hub.ui.output import ImageRenderer, OutputParser
from hub.ui.output.graphics import KittyGraphics


class GraphicsTests(unittest.TestCase):
    def test_capability_and_protocol_chunking(self):
        self.assertFalse(KittyGraphics.supported({}))
        self.assertFalse(KittyGraphics.supported({'TERM': 'xterm-kitty', 'TMUX': 'active'}))
        self.assertTrue(KittyGraphics.supported({'TERM': 'xterm-kitty'}))
        commands = KittyGraphics.encode(b'a' * 9000, 17).split('\x1b_G')[1:]
        self.assertGreater(len(commands), 1)
        for command in commands:
            payload = command.partition(';')[2].removesuffix('\x1b\\')
            self.assertLessEqual(len(payload), 4096)
        self.assertIn('I=17', commands[0])
        self.assertIn('m=0;', commands[-1])

    def test_paint_hide_resize_and_close_address_only_owned_number(self):
        raw, cursors = [], []
        window = object()
        output = SimpleNamespace(write_raw=raw.append, cursor_goto=lambda y, x: cursors.append((y, x)),
            get_size=lambda: SimpleNamespace(columns=80, rows=24), flush=lambda: None)
        positions = {window: SimpleNamespace(xpos=70, ypos=20, width=40, height=20)}
        app = SimpleNamespace(output=output, is_done=False,
            renderer=SimpleNamespace(_last_screen=SimpleNamespace(visible_windows_to_write_positions=positions)))
        app.before_render, app.after_render = Event(app), Event(app)
        painter = KittyGraphics(app, window, 'PNG-PAYLOAD', 17)
        app.after_render.fire()
        self.assertEqual(cursors, [(20, 70)])
        self.assertIn('c=10,r=4', ''.join(raw))
        self.assertEqual(raw.count('PNG-PAYLOAD'), 1)
        app.before_render.fire()
        app.after_render.fire()
        self.assertEqual(raw.count('PNG-PAYLOAD'), 1)
        positions.clear()
        app.before_render.fire()
        app.after_render.fire()
        self.assertFalse(painter.uploaded)
        positions[window] = SimpleNamespace(xpos=0, ypos=0, width=10, height=5)
        app.after_render.fire()
        self.assertEqual(raw.count('PNG-PAYLOAD'), 2)
        painter.close()
        length = len(raw)
        app.after_render.fire()
        self.assertEqual(len(raw), length)
        self.assertNotIn('a=d,d=A', ''.join(raw))
        self.assertTrue(all('I=17' in command for command in raw if 'a=d' in command))

    def test_native_image_reuses_source_authority_and_never_fetches_remote_urls(self):
        renderer, parser = ImageRenderer(), OutputParser()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGB', (2, 2)).save(root / 'image.png')
            block = parser.parse('<hub-image src="image.png" />')[0]
            self.assertTrue(renderer.read_png(block, root).startswith(b'\x89PNG'))
            for source in ('https://example.invalid/image.png', '../outside.png', 'artifact:unknown'):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    renderer.read_png(parser.parse(f'<hub-image src="{source}" />')[0], root)
        renderer.close()
