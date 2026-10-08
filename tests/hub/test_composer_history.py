"""Request recall uses arrows without stealing completion or multiline editing."""

import asyncio
from dataclasses import replace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import Completion
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.component_commands import component_commands
from hub.model import ChatMessage
from tests.hub.test_mockup import SizedOutput, eventually


class ComposerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_arrow_recall_edit_completion_and_session_isolation(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.sessions[0] = replace(view.sessions[0], messages=(
                ChatMessage("user", "first"), ChatMessage("assistant", "not an input"),
                ChatMessage("user", "second\nline")))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                view.composer.text = ""
                pipe.send_text("\x1b[A")
                await eventually(lambda: view.composer.text == "second\nline")
                self.assertEqual(view.composer.buffer.cursor_position, len(view.composer.text))
                pipe.send_text("\x1b[A")
                await eventually(lambda: view.composer.text == "first")
                pipe.send_text("\x1b[B")
                await eventually(lambda: view.composer.text == "second\nline")
                pipe.send_text("\x1b[B")
                await eventually(lambda: view.composer.text == "")
                pipe.send_text("\x1b[A")
                await eventually(lambda: view.composer.text == "second\nline")
                pipe.send_text("!")
                await eventually(lambda: view.composer.text.endswith("!"))
                self.assertFalse(view.input_history.browsing)
                pipe.send_text("\x1b[A")
                await eventually(lambda: view.composer.buffer.document.cursor_position_row == 0)
                self.assertEqual(view.composer.text, "second\nline!")
                view.composer.text = ""
                with set_app(app):
                    view.composer.buffer._set_completions([Completion("one"), Completion("two")])
                pipe.send_text("\x1b[B")
                await eventually(lambda: view.composer.buffer.complete_state.current_completion is not None)
                self.assertFalse(view.input_history.available)
                view.composer.buffer.cancel_completion()
                view.composer.text = ""
                with set_app(app):
                    view.select(1)
                view.composer.text = ""
                with set_app(app):
                    view.input_history.move(-1)
                self.assertNotIn(view.composer.text, ("first", "second\nline"))
                view.question.item = {"request": {"id": "question"}}
                view.composer.text = ""
                self.assertFalse(view.input_history.available)
                view.question.item = None
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()

    def test_tools_has_no_duplicate_alias(self):
        self.assertEqual(component_commands(("tools",)), {"tools": "tools"})
        # A genuinely registered custom component retains its own name.
        self.assertEqual(component_commands(("tools", "tool")), {"tools": "tools", "tool": "tool"})
