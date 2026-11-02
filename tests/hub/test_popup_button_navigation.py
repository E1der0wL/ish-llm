"""Popup Tab visits action groups; arrows select actions without activating them."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.widgets import Label

from examples.hub.preview import create_preview
from hub.widget.actions import ActionBar
from hub.widget.controls import Button, TextArea
from hub.widget.dialog import Dialog, PopupInput
from hub.widget.forms.workflow_editor import WorkflowEditor
from hub.widget.workflow.bindings import WorkflowBindings
from tests.hub.support import SizedOutput, eventually


class PopupButtonNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput(132, 40))
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.visible)

    async def asyncTearDown(self):
        if not self.task.done():
            self.app.exit()
        try:
            await self.task
        finally:
            self.view.close()

    async def key(self, text):
        processed = asyncio.Event()
        def done(_):
            processed.set()
        self.app.key_processor.after_key_press += done
        try:
            self.pipe.send_text(text)
            await asyncio.wait_for(processed.wait(), 3)
        finally:
            self.app.key_processor.after_key_press -= done

    def focused(self, control):
        self.assertIs(self.app.layout.current_control, control)

    async def test_tab_skips_other_buttons_and_returns_to_last_choice(self):
        clicked = Mock()
        field = TextArea(multiline=False, height=1)
        edit = [Button(name, handler=clicked) for name in ('Create', 'Apply', 'Delete')]
        footer = [Button(name, handler=clicked) for name in ('Confirm', 'Cancel')]
        popup = Dialog(body=HSplit([field, ActionBar(edit, lambda: 70)]), buttons=footer)
        with set_app(self.app):
            self.view.dialogs.show(popup, field)
        await self.key('\t')
        self.focused(edit[0].control)
        await self.key('\x1b[C')
        self.focused(edit[1].control)
        await self.key('\t')
        self.focused(footer[0].control)
        await self.key('\x1b[C')
        self.focused(footer[1].control)
        await self.key('\x1b[Z')
        self.focused(edit[1].control)
        await self.key('\x1b[D')
        self.focused(edit[0].control)
        await self.key('\x1b[Z')
        self.focused(field.control)
        clicked.assert_not_called()

    async def test_button_only_popup_does_not_use_tab_or_vertical_arrows_for_selection(self):
        buttons = [Button(name) for name in ('Yes', 'No', 'Cancel')]
        with set_app(self.app):
            self.view.dialogs.show(Dialog(body=Label('Confirm'), buttons=buttons), buttons[1])
        for key in ('\t', '\x1b[Z', '\x1b[A', '\x1b[B'):
            await self.key(key)
            self.focused(buttons[1].control)
        await self.key('\x1b[C')
        self.focused(buttons[2].control)

    async def test_hidden_group_is_not_a_tab_destination(self):
        field = TextArea(multiline=False, height=1)
        visible = True
        edit = [Button('Create'), Button('Delete')]
        footer = [Button('Confirm'), Button('Cancel')]
        popup = Dialog(body=HSplit([
            field, ConditionalContainer(ActionBar(edit, lambda: 70), Condition(lambda: visible)),
        ]), buttons=footer)
        with set_app(self.app):
            self.view.dialogs.show(popup, edit[1])
        await self.key('\t')
        self.focused(footer[0].control)
        visible = False
        await self.key('\x1b[Z')
        self.focused(field.control)
        visible = True
        await self.key('\t')
        self.focused(edit[0].control)

    async def test_modal_reader_receiver_can_reach_action_group(self):
        receiver = PopupInput().receiver
        buttons = [Button('Find'), Button('Close')]
        with set_app(self.app):
            self.view.dialogs.show(Dialog(body=Window(receiver, height=0), buttons=buttons), receiver)
        await self.key('\t')
        self.focused(buttons[0].control)
        await self.key('\x1b[C')
        self.focused(buttons[1].control)
        await self.key('\t')
        self.focused(receiver)

    async def test_workflow_binding_groups_remain_separate_with_parent_retained(self):
        with set_app(self.app):
            manager = SimpleNamespace(name='workflows', view=self.view, request=Mock(), list=TextArea())
            editor = WorkflowEditor(manager)
            self.view.dialogs.show(editor, editor.canvas)
            bindings = WorkflowBindings(editor)
            editor.dialogs.open('Bindings', bindings.tabs, bindings.apply, bindings.actions.buttons[0])
            popup = self.view._dialog
        for button in bindings.actions.buttons:
            with set_app(self.app):
                self.app.layout.focus(button)
            await self.key('\t')
            self.focused(popup._buttons[0].control)
            await self.key('\x1b[Z')
            self.focused(button.control)
        with set_app(self.app):
            self.app.layout.focus(bindings.tabs.buttons[0])
        await self.key('\x1b[C')
        self.assertEqual(bindings.direction, 'outputs')
        await self.key('\t')
        self.focused(bindings.list)
        await self.key('\x1b[Z')
        self.focused(bindings.tabs.buttons[1].control)
        self.assertIs(self.view.dialogs.parents[-1][0], editor)
