"""Choice navigation preserves PTK selection and emits one change notification."""

import asyncio
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.controls import CheckboxList, RadioList
from tests.hub.support import SizedOutput, eventually


class ChoiceControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_selection_notifies_once_and_preserves_radio_behavior(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                seen = []
                choices = CheckboxList([("a", "A"), ("b", "B")], on_select=seen.append)
                with set_app(app):
                    view.open_dialog("Check", choices, focus=choices)
                expected = []
                for key, value, checked in (("\x1b[B", "b", []), ("\r", "b", ["b"]),
                                            (" ", "b", []), ("\x1b[A", "a", [])):
                    expected.append(value)
                    pipe.send_text(key)
                    await eventually(lambda: len(seen) >= len(expected))
                    self.assertEqual(seen, expected)
                    self.assertEqual(choices.current_values, checked)
                radio = RadioList([("a", "A"), ("b", "B")])
                with set_app(app):
                    view.close_dialog()
                    view.open_dialog("Pick", radio, focus=radio)
                pipe.send_text("\x1b[B\r")
                await eventually(lambda: radio.current_value == "b")
                pipe.send_text("\x1b[A ")
                await eventually(lambda: radio.current_value == "a")
                self.assertIs(app.layout.current_control, radio.control)
            finally:
                app.exit()
                await task
                view.close()
