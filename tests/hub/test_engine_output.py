"""Home keeps its chrome while an engine owns its output and interactions."""

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from examples.hub.preview import create_preview
from hub.config.view_state import ReadingPosition
from hub.model import ChatMessage
from hub.widget.output.engine import EngineOutput
from hub.widget.output.tag_bar import OutputTag
from hub.widget.output.loop import LoopOutput
from tests.hub.support import SizedOutput, eventually


class AlternativeOutput(EngineOutput):
    """An engine supplies node tags without depending on the Loop transcript."""

    def __init__(self, view):
        self.window = Window(FormattedTextControl("Alternative engine output"))
        self.position = ReadingPosition("alternative", 0, 0)
        super().__init__(view, self.window, heading=lambda: "Nodes")
        for name in ("set_messages", "update_messages", "restore_position", "set_follow_tail",
                     "set_theme", "set_file_root", "scroll", "invoke", "deactivate", "release", "expand_tag"):
            setattr(self, name, Mock(wraps=getattr(self, name)))

    def tag_entries(self):
        return (OutputTag("node:first", "First node", 0, "◆", "first"),
                OutputTag("node:second", "Second node", 40, "◆", "second"))

    def select_tag(self, tag):
        self.position = ReadingPosition(tag.id, 0, tag.position)

    def reading_position(self):
        return self.position

    def restore_position(self, position):
        self.position = position or ReadingPosition()

    def set_messages(self, messages, position=None):
        self.restore_position(position)

    def update_messages(self, messages, *, auto_scroll=None): pass
    def set_follow_tail(self, enabled): pass
    def set_theme(self, theme): pass
    def set_file_root(self, path): pass
    def scroll(self, key): pass
    def release(self): pass


class EngineOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input = create_pipe_input()
        self.pipe = self.input.__enter__()
        self.size = SizedOutput()
        self.app, self.view, _ = create_preview(input=self.pipe, output=self.size)
        self.task = asyncio.create_task(self.app.run_async())
        self.pipe.send_text("\x11")
        await eventually(lambda: self.view.visible)
        self.view.sessions[self.view.selected] = replace(self.view.sessions[self.view.selected], messages=(
            ChatMessage("user", "Keep this request", id="u"),
            ChatMessage("assistant", "```python\n" + "print('item')\n" * 90 + "```", id="a")))
        self.view.output.set_messages(self.view.sessions[self.view.selected].messages)
        self.app.invalidate()
        await eventually(lambda: bool(self.view.output.objects))

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.input.__exit__(None, None, None)

    async def paint(self):
        painted = asyncio.Event()
        def done(_):
            painted.set()
        self.app.after_render += done
        try:
            self.app.invalidate()
            await asyncio.wait_for(painted.wait(), 3)
        finally:
            self.app.after_render -= done
        return self.app.renderer._last_screen.visible_windows_to_write_positions

    async def test_tags_share_only_output_height_and_do_not_narrow_input_or_status(self):
        view, output = self.view, self.view.output
        self.assertIsInstance(output, LoopOutput)
        self.assertNotIn("tags", vars(view))
        self.assertIs(output.tags.output, output)
        for columns, rows in ((132, 40), (80, 24), (60, 14)):
            with self.subTest(size=(columns, rows)):
                self.size.columns, self.size.rows = columns, rows
                self.app._on_resize()
                with set_app(self.app):
                    output.deactivate()
                    view._focus_composer()
                before = await self.paint()
                composer = before[view.composer.window]
                status = before[view.chat_page.status]
                with set_app(self.app):
                    output.tags.open()
                after = await self.paint()
                tags, text = after[output.tags.window], after[output.transcript.window]
                header = after[output.header]
                self.assertEqual((tags.ypos, tags.height), (text.ypos + 1, text.height - 1))
                self.assertEqual(after[output.tags.heading].ypos, text.ypos)
                self.assertEqual(header.xpos, composer.xpos - 1)
                self.assertEqual(text.xpos, composer.xpos - 1)
                self.assertEqual(tags.xpos + tags.width, header.xpos + header.width)
                self.assertEqual(header.xpos + header.width, composer.xpos + composer.width)
                self.assertLessEqual(tags.ypos + tags.height, after[view.composer.window].ypos - 2)
                for window, position in ((view.composer.window, composer), (view.chat_page.status, status)):
                    actual = after[window]
                    self.assertEqual((actual.xpos, actual.width, actual.ypos),
                                     (position.xpos, position.width, position.ypos))
                self.assertFalse(output.transcript.control.is_focusable())
                self.assertIs(self.app.layout.current_control, output.tags.control)

    async def test_replacing_output_dismisses_owned_popup_and_preserves_home(self):
        view, old = self.view, self.view.output
        view.composer.text = "preserve draft"
        chrome = (view.chat_page, view.composer, view.auxiliary, view._sidebar, view.profile_bar)
        with set_app(self.app):
            old.tags.open()
            old.tags.select(old.objects[0].id)
            old.tags.expand()
        self.assertIsNotNone(view._dialog)
        replacement = AlternativeOutput(view)
        with set_app(self.app):
            view.set_output(replacement)
        positions = await self.paint()
        self.assertIn(replacement.window, positions)
        self.assertNotIn(old.tags.window, positions)
        self.assertNotIn(old.transcript.window, positions)
        self.assertIsNone(view._dialog)
        self.assertFalse(old.tags.visible)
        self.assertTrue(old._closed)
        self.assertEqual(chrome, (view.chat_page, view.composer, view.auxiliary, view._sidebar, view.profile_bar))
        self.assertEqual(view.composer.text, "preserve draft")
        self.assertIs(self.app.layout.current_control, view.composer.control)
        replacement.set_messages.assert_called_once_with(view.sessions[view.selected].messages,
                                                         view._positions[view._draft_key(view.selected)])
        self.pipe.send_text("\x1b\x1b[F")  # Alt+End belongs to the current output.
        await eventually(lambda: replacement.scroll.called)
        self.assertEqual(replacement.scroll.call_args.args, ("end",))
        with set_app(self.app):
            view.auxiliary.buttons["tags"].handler()
            view.auxiliary.buttons["find"].handler()
            view.toggle_details()
            view.set_theme(view.theme)
            view.preparing.paint()
            view.select(view.selected)
        self.assertEqual([call.args[0] for call in replacement.invoke.call_args_list], ["tags", "find", "details"])
        self.assertTrue(replacement.update_messages.called)
        self.assertTrue(replacement.deactivate.called)
        self.assertEqual(view.composer.text, "preserve draft")

    async def test_closing_search_clears_highlights_and_replacement_detaches_search_keys(self):
        view, old = self.view, self.view.output
        with set_app(self.app):
            old.search.open()
        self.pipe.send_text("print")
        await eventually(lambda: old.transcript.control.search_query == "print")
        self.pipe.send_text("\x1b")
        await eventually(lambda: view._dialog is None)
        self.assertEqual(old.transcript.control.search_query, "")
        with set_app(self.app):
            old.search.open()
            replacement = AlternativeOutput(view)
            view.set_output(replacement)
        self.assertIsNone(view._dialog)
        self.assertIsNone(old.floating_dialog)
        self.assertFalse(old.shortcuts.active())
        self.assertIsNone(view.output.modal_container)
        with set_app(self.app):
            view.help_dialog()
            popup, focus = view._dialog, self.app.layout.current_control
            view.set_output(AlternativeOutput(view))
        self.assertIs(view._dialog, popup)
        self.assertIs(self.app.layout.current_control, focus)

    async def test_session_change_releases_hidden_tag_focus_and_still_accepts_input(self):
        view = self.view
        with set_app(self.app):
            view.output.tags.open()
            view.select((view.selected + 1) % len(view.sessions))
        self.assertFalse(view.output.tags.visible)
        self.assertIs(self.app.layout.current_control, view.composer.control)
        self.pipe.send_text("new draft")
        await eventually(lambda: view.composer.text == "new draft")

    async def test_common_tags_use_engine_entries_navigation_and_expand(self):
        view = self.view
        output = AlternativeOutput(view)
        with set_app(self.app):
            view.set_output(output)
            output.restore_position(ReadingPosition(line=35))
            output.tags.open()
        await self.paint()
        self.assertFalse(hasattr(output, "transcript"))
        self.assertEqual(output.tags.selected, "node:second")
        self.assertEqual(output.reading_position().line, 35)  # Opening never navigates.
        self.pipe.send_text("\x1b[A")
        await eventually(lambda: output.tags.selected == "node:first")
        self.assertEqual(output.reading_position().line, 0)
        self.pipe.send_text("\r")
        await eventually(lambda: output.expand_tag.called)
        self.assertEqual(output.expand_tag.call_args.args[0].id, "node:first")
        with set_app(self.app):
            output.tags.collapse.handler()
        self.assertFalse(output.tags.visible)
        self.assertIs(self.app.layout.current_control, view.composer.control)

    async def test_open_tracks_viewport_without_jumping_or_reusing_old_selection(self):
        output = self.view.output
        messages = tuple(message for index in range(3) for message in (
            ChatMessage("user", f"Request {index}", id=f"u{index}"),
            ChatMessage("assistant", "```py\n" + "print('item')\n" * 45 + "```", id=f"a{index}")))
        output.set_messages(messages)
        await self.paint()
        await eventually(lambda: len(output.objects) == 3)
        for index in (1, 0, 2):
            with set_app(self.app):
                output.deactivate()
                self.view._focus_composer()
            await self.paint()
            await eventually(lambda: not output.transcript.control.restoring)
            item = output.objects[index]
            with set_app(self.app):
                output.transcript.control.scroll_to(item.start_line + 4)
            await self.paint()
            before = output.reading_position()
            with set_app(self.app):
                output.tags.open()
            await self.paint()
            await eventually(lambda: not output.transcript.control.restoring)
            self.assertEqual(output.reading_position(), before)
            self.assertEqual(output.tags.selected, item.id)
        with set_app(self.app):
            output.scroll("home")
        await self.paint()
        self.assertEqual(output.tags.selected, "u0")
