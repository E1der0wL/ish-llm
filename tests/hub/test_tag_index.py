"""Tag labels, icon styles and request-only scroll anchors in the real UI."""

import asyncio
from dataclasses import replace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth

from examples.hub.preview import create_preview
from hub.config.theme import HubTheme
from hub.model import ChatMessage
from hub.ui.output.labels import ellipsize
from hub.widget.transcript.conversation import ConversationControl
from tests.hub.support import SizedOutput, eventually


class TagLabelTests(unittest.TestCase):
    def test_objects_have_icons_and_content_or_explicit_title_without_changing_raw(self):
        source = ('```py\nprint("hello")\n```\n\n| 이름 | 값 |\n| --- | --- |\n| 가 | 1 |\n\n'
                  '<hub-code title="내가 지정한 제목">print(2)</hub-code>')
        control = ConversationControl((ChatMessage("assistant", source, id="a"),), HubTheme(icon_style="nerd"))
        try:
            control.create_content(80, 20)
            self.assertEqual([item.icon for item in control.objects], ["", "", ""])
            self.assertTrue(control.objects[0].title.startswith('print("hello")'))
            self.assertIn("이름", control.objects[1].title)
            self.assertEqual(control.objects[2].title, "내가 지정한 제목")
            raw = [item.raw for item in control.objects]
            control.theme = replace(control.theme, icon_style="unicode")
            control.create_content(40, 20)
            self.assertEqual([item.icon for item in control.objects], ["⌘", "▦", "⌘"])
            self.assertEqual([item.raw for item in control.objects], raw)
        finally:
            control.renderers.close()

    def test_ellipsis_respects_wide_cells_and_control_characters(self):
        for text in ("가나다 " * 100, "long code " * 100, "😊" * 100):
            for width in (0, 1, 2, 17, 30):
                label = ellipsize(text, width)
                self.assertLessEqual(get_cwidth(label), width)
                if width:
                    self.assertTrue(label.endswith("…"))
        self.assertEqual(ellipsize("short\n title", 20), "short title")


class TagRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_entries_scroll_without_expanding_and_keep_chronological_order(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, _ = create_preview(input=pipe, output=output)
            messages = (ChatMessage("user", "사용자 요청이 아주 깁니다 " * 20, id="u1"),
                        ChatMessage("assistant", "```py\nprint('first')\n```\n\n" + "response\n\n" * 40, id="a1"),
                        ChatMessage("user", "두 번째 요청", id="u2"),
                        ChatMessage("assistant", '<hub-code title="긴 코드 제목입니다 ' + "가" * 60 + '">print(2)</hub-code>', id="a2"))
            view.transcript.set_messages(messages)
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11")
                await eventually(lambda: view.visible and len(view.transcript.control.objects) == 2)
                pipe.send_text("\x14")
                await eventually(lambda: app.layout.current_control is view.tags.control)
                with set_app(app):
                    items = view.tags.items()
                    self.assertEqual([item.key for item in items], ["u1", "a1:block:0", "u2", "a2:block:0"])
                    self.assertTrue(items[0].title.startswith("사용자 요청"))
                    self.assertTrue(items[0].title.endswith("…"))
                    self.assertTrue(items[-1].title.startswith("  긴 코드 제목"))
                    self.assertTrue(items[-1].title.endswith("…"))
                    self.assertEqual([item.margin_top for item in items], [1, 0, 1, 0])
                    self.assertEqual(view.tags.control.cursor_position().y, 1)
                    content = view.tags.control.create_content(view.tags.width() - 1, 20)
                    rows = ["".join(fragment[1] for fragment in content.get_line(row))
                            for row in range(content.line_count)]
                    self.assertEqual(rows[0].strip(), "")
                    self.assertEqual(rows[3].strip(), "")
                    self.assertIn(items[1].title, rows[2])
                    for row, selected in ((0, "u1"), (2, "a1:block:0"), (3, "a1:block:0"),
                                          (4, "u2"), (1, "u1")):
                        view.tags.control.mouse_handler(MouseEvent(Point(4, row),
                            MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
                        self.assertEqual(view.tags.selected, selected)
                        if view._dialog is not None:
                            view.close_dialog()
                    for item in items:
                        self.assertLessEqual(get_cwidth(item.title), view.tags.width() - 4)
                for key in ("\r", "\t", " "):
                    pipe.send_text(key)
                    await asyncio.sleep(.04)
                    self.assertIsNone(view._dialog)
                    self.assertIs(app.layout.current_control, view.tags.control)
                pipe.send_text("\x1b[B\x1b[B")
                await eventually(lambda: view.tags.selected == "u2" and view.transcript.control.top_line > 0)
                self.assertEqual(view.tags.control.cursor_position().y, 4)
                position = dict(view.transcript.control._anchors)["u2"]
                self.assertEqual(view.transcript.control.top_line,
                                 min(position, len(view.transcript.control._lines) - view.transcript.control._height))
                pipe.send_text("\x1b[B\r")
                await eventually(lambda: view._dialog is not None)
            finally:
                app.exit()
                await task
                view.output_renderers.close()
