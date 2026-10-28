"""Minimizing Hub suspends popups without cancelling their editing or resources."""

import asyncio
import json
import unittest
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit

from examples.hub.preview import install_preview
from hub.widget.controls import Button, CheckboxList, TextArea
from hub.widget.dialog import Dialog
from hub.widget.output.reader import ReadOnlyDialog
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_ish_integration import Prompt


class PopupSuspendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.prompt = Prompt(input=self.pipe, output=SizedOutput())
        self.installation = install_preview(self.prompt)
        self.app, self.view = self.prompt.app, self.installation.view
        self.task = asyncio.create_task(self.prompt.prompt_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running)

    async def asyncTearDown(self):
        if not self.task.done():
            self.app.exit(result="")
        await self.task
        self.installation.close()

    async def paint(self):
        painted = asyncio.Event()
        def done(app):
            painted.set()
        self.app.after_render += done
        try:
            self.app.invalidate()
            await asyncio.wait_for(painted.wait(), 3)
        finally:
            self.app.after_render -= done

    async def minimize(self):
        self.pipe.send_text("\x11")
        await eventually(lambda: not self.view.visible)
        self.assertIs(self.app.current_buffer, self.prompt.default_buffer)

    async def reopen(self):
        self.pipe.send_text("\x11")
        await eventually(lambda: self.view.visible)
        await self.paint()

    async def test_input_cursor_selection_and_cancel_survive_repeated_minimize(self):
        field = TextArea(text="unfinished draft", multiline=True)
        cancelled, disposed = Mock(), Mock()
        with set_app(self.app):
            self.view.open_dialog("Edit", field, accept=lambda: None, focus=field, on_cancel=cancelled)
            popup = self.view._dialog
            popup.dispose = disposed
            field.buffer.cursor_position = 2
            field.buffer.start_selection()
            field.buffer.cursor_position = 6
        selection = field.buffer.selection_state
        for _ in range(2):
            await self.minimize()
            self.assertIs(self.view._dialog, popup)
            self.pipe.send_text("shell ")
            await eventually(lambda: self.prompt.default_buffer.text.endswith("shell "))
            await self.reopen()
            self.assertIs(self.view._dialog, popup)
            self.assertIs(self.app.layout.current_control, field.control)
            self.assertEqual(field.text, "unfinished draft")
            self.assertEqual(field.buffer.cursor_position, 6)
            self.assertIs(field.buffer.selection_state, selection)
            cancelled.assert_not_called()
            disposed.assert_not_called()
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        cancelled.assert_called_once()
        disposed.assert_called_once()
        self.assertIs(self.app.layout.current_control, self.view.composer.control)

    async def test_settings_popup_keeps_checked_items_scroll_and_button_focus(self):
        choices = CheckboxList([(str(i), f"Item {i}") for i in range(40)])
        choices.window.height = 6
        choices.current_values = ["2", "15"]
        choices._selected_index = 15
        close = Button("Close", handler=self.view.close_dialog)
        popup = Dialog(title="Choices", body=HSplit([choices]), buttons=[close])
        with set_app(self.app):
            self.view.toggle_settings()
            previous = self.app.layout.current_control
            self.view.dialogs.show(popup, choices)
        await self.paint()
        offset = choices.window.vertical_scroll
        self.assertGreater(offset, 0)
        await self.minimize()
        await self.reopen()
        self.assertTrue(self.view.settings_open)
        self.assertIs(self.app.layout.current_control, choices.control)
        self.assertEqual((choices.current_values, choices._selected_index, choices.window.vertical_scroll),
                         (["2", "15"], 15, offset))
        with set_app(self.app):
            self.app.layout.focus(close)
        await self.minimize()
        await self.reopen()
        self.assertIs(self.app.layout.current_control, close.control)
        self.pipe.send_text("\r")
        await eventually(lambda: self.view._dialog is None)
        self.assertIs(self.app.layout.current_control, previous)

    async def test_narrow_home_preserves_sidebar_as_popup_return_target(self):
        self.app.output.columns = 80
        with set_app(self.app):
            self.view._focus_sessions()
            field = TextArea(text="Session name")
            self.view.open_dialog("Rename", field, focus=field)
        await self.paint()
        self.assertTrue(self.view._sidebar.filter())
        await self.minimize()
        await self.reopen()
        self.assertTrue(self.view._sidebar.filter())
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        self.assertIs(self.app.layout.current_control, self.view._session_control)
        self.assertTrue(self.view._sidebar.filter())

    async def test_popup_survives_shell_submission_and_next_prompt_cycle(self):
        field = TextArea(text="unfinished edit")
        with set_app(self.app):
            self.view.open_dialog("Edit", field, focus=field)
        popup = self.view._dialog
        await self.minimize()
        self.pipe.send_text("shell command\r")
        self.assertEqual(await asyncio.wait_for(self.task, 3), "shell command")
        self.assertIs(self.view._dialog, popup)
        self.task = asyncio.create_task(self.prompt.prompt_async())
        await eventually(lambda: self.app.is_running)
        await self.reopen()
        self.assertIs(self.view._dialog, popup)
        self.assertIs(self.app.layout.current_control, field.control)
        self.assertEqual(field.text, "unfinished edit")

    async def test_reader_scroll_and_resources_are_retained_until_close(self):
        popup = ReadOnlyDialog("Log", "\n".join(f"line {i}" for i in range(100)),
            self.view._rows, self.view._columns, self.view.close_dialog, self.view.t)
        disposed = popup.dispose = Mock()
        with set_app(self.app):
            self.view.dialogs.show(popup, popup.receiver)
        await self.paint()
        popup.output.scroll("home")
        popup.output.scroll("pagedown")
        position = popup.output.top_line
        self.assertGreater(position, 0)
        await self.minimize()
        await self.reopen()
        self.assertIs(self.view._dialog, popup)
        self.assertEqual(popup.output.top_line, position)
        self.assertIs(self.app.layout.current_control, popup.receiver)
        disposed.assert_not_called()
        await self.minimize()
        with set_app(self.app):
            self.view.close()
        disposed.assert_called_once()
        self.assertIsNone(self.view._dialog)

    async def test_search_popup_and_highlights_survive_until_explicit_close(self):
        with set_app(self.app):
            self.view.output.find()
        search = self.view.output.search
        self.pipe.send_text("search query")
        await eventually(lambda: search.input.text == "search query")
        search.input.buffer.cursor_position = 3
        popup = self.view._dialog
        await self.minimize()
        self.assertTrue(search.active)
        await self.reopen()
        self.assertIs(self.view._dialog, popup)
        self.assertIs(self.app.layout.current_control, search.input.control)
        self.assertEqual(search.input.buffer.cursor_position, 3)
        self.assertEqual(self.view.output.transcript.control.search_query, "search query")
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        self.assertFalse(search.active)
        self.assertEqual(self.view.output.transcript.control.search_query, "")

    async def test_hidden_loading_replacement_and_close_never_steal_shell_focus(self):
        with set_app(self.app):
            self.view.dialogs.loading("Loading")
        await self.minimize()
        field = TextArea(text="loaded")
        popup = Dialog(title="Loaded", body=field, buttons=[Button("Close", handler=self.view.close_dialog)])
        with set_app(self.app):
            self.view.dialogs.show(popup, field)
        self.assertIs(self.app.current_buffer, self.prompt.default_buffer)
        self.pipe.send_text("shell input")
        await eventually(lambda: self.prompt.default_buffer.text == "shell input")
        await self.reopen()
        self.assertIs(self.view._dialog, popup)
        self.assertIs(self.app.layout.current_control, field.control)
        await self.minimize()
        with set_app(self.app):
            self.view.close_dialog()
        self.assertIs(self.app.current_buffer, self.prompt.default_buffer)
        await self.reopen()
        self.assertIsNone(self.view._dialog)
        self.assertIs(self.app.layout.current_control, self.view.composer.control)

    async def test_agent_editor_loads_while_hidden_and_retains_editing_and_parent(self):
        from hub.backend.project.management_values import EditorRecord
        from hub.widget.forms.definition_manager import DefinitionManager
        from llm.engines.loop import LoopEngine
        from llm.engines.registry import EngineRegistry
        registry = EngineRegistry()
        registry.register("loop", LoopEngine())
        pending = []
        def request(action, name, version, done):
            pending.append(done)
        with set_app(self.app):
            manager = DefinitionManager(self.view, request, name="agents", edit_text=Mock())
            self.view.dialogs.show(manager, manager.path)
            manager.agent_editor("assistant", EditorRecord.create(json.dumps({
                "engine": "loop", "completion": {"model": "test/main"}}), "revision"))
        editor = self.view._dialog
        await self.minimize()
        with set_app(self.app):
            pending.pop()({"schema": registry.describe_agent("loop"), "engines": ["loop"],
                           "storage_version": 1})
        self.assertIs(self.app.current_buffer, self.prompt.default_buffer)
        await self.reopen()
        self.assertIs(self.view._dialog, editor)
        with set_app(self.app):
            editor.workspace.select("completion")
            field = editor.form.fields[("completion", "model")]
            self.app.layout.focus(field.input)
            field.begin()
            field.input.text = "test/unsaved"
            field.input.buffer.cursor_position = 4
        await self.minimize()
        await self.reopen()
        self.assertIs(self.app.layout.current_control, field.input.control)
        self.assertTrue(field.editing)
        self.assertEqual((field.input.text, field.input.buffer.cursor_position), ("test/unsaved", 4))
        with set_app(self.app):
            editor.close()
        self.assertIs(self.view._dialog, manager)

    async def test_tag_inspector_and_sidebar_selection_survive_minimize(self):
        from hub.model import ChatMessage
        with set_app(self.app):
            self.view.output.transcript.set_messages((ChatMessage("assistant", "```python\nprint(1)\n```", id="a"),))
        await self.paint()
        await eventually(lambda: bool(self.view.output.objects))
        with set_app(self.app):
            self.view.output.tags.open()
            tag = self.view.output.tag_entries()[0]
            self.view.output.tags.select(tag.id)
            self.view.output.expand_tag(tag)
        popup = self.view._dialog
        await self.minimize()
        await self.reopen()
        self.assertIs(self.view._dialog, popup)
        self.assertIs(self.view.output.inspector, popup)
        self.assertTrue(self.view.output.tags.visible)
        self.assertEqual(self.view.output.tags.selected, tag.id)
        self.assertIs(self.app.layout.current_control, popup.receiver)
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        self.assertIs(self.app.layout.current_control, self.view.output.tags.control)
