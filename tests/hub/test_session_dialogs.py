"""Creation is name-only; cloning previews the selected durable boundary."""

import asyncio
import unittest
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.dialog import PopupSize
from hub.widget.forms.session_form import SessionNameForm, SessionCloneForm
from tests.hub.support import SizedOutput, eventually, render_container


def turns():
    return [dict(id=f'u{i}', text=f'Request {i}', response=f'Response {i}',
                 status='completed', engine='loop', elapsed=1, error='',
                 time='2026-10-29T00:00:00+00:00', agent_id='assistant', runs=[])
            for i in range(2)]


class SessionDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_creation_does_not_load_history_and_clone_previews_boundary(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput(100, 36))
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            view.on_new, view.on_turns = Mock(), Mock()
            try:
                await eventually(lambda: app.is_running)
                source = view.sessions[view.selected].id
                with set_app(app):
                    view.session_actions.create()
                    dialog = view._dialog
                    self.assertEqual(dialog.size, PopupSize.SMALL)
                    self.assertIsInstance(dialog.popup_body, SessionNameForm)
                    self.assertNotIsInstance(dialog.popup_body, SessionCloneForm)
                    self.assertFalse(hasattr(dialog.popup_body, 'mode'))
                    view.on_turns.assert_not_called()
                pipe.send_text('\r')
                await eventually(lambda: view._dialog is None)
                view.on_new.assert_called_once_with('new', '', source)
                with set_app(app):
                    view.session_actions.create('clone', 'Branch')
                    dialog = view._dialog
                    form = dialog.popup_body
                    self.assertEqual(dialog.size, PopupSize.MEDIUM)
                    self.assertIsInstance(form, SessionCloneForm)
                    view.on_turns.assert_called_once()
                    view.on_turns.call_args.args[1](turns())
                    self.assertIn('Response 1', form.preview.text)
                    app.layout.focus(form.boundary)
                pipe.send_text('\x1b[B')
                await eventually(lambda: form.boundary.current_value == 'u0')
                self.assertIn('Request 0', form.preview.text)
                self.assertIn('Response 0', form.preview.text)
                self.assertNotIn('Response 1', form.preview.text)
                with set_app(app):
                    text = '\n'.join(render_container(dialog, 100, 36))
                    self.assertLess(text.index('이름 (선택)'), text.index('복제할 마지막'))
                    self.assertLess(text.index('복제할 마지막'), text.index('대화 내용 미리 보기'))
                    app.layout.focus(form.name)
                pipe.send_text('\r')
                await eventually(lambda: view._dialog is None)
                view.on_new.assert_called_with('clone', 'Branch', source, 'u0')
            finally:
                app.exit()
                await task
                view.close()

    async def test_late_clone_history_cannot_replace_a_new_creation_dialog(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            view.on_turns, view.on_new = Mock(), Mock()
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.session_actions.create('clone')
                    old = view._dialog
                    old.popup_body.name.buffer.validate_and_handle()
                    self.assertIs(view._dialog, old)
                    view.on_new.assert_not_called()
                    loaded = view.on_turns.call_args.args[1]
                    view.close_dialog()
                    view.session_actions.create()
                    new = view._dialog
                    loaded(turns())
                    self.assertIs(view._dialog, new)
                    self.assertEqual(old.popup_body.rows, [])
                    self.assertFalse(hasattr(new.popup_body, 'boundary'))
            finally:
                app.exit()
                await task
                view.close()
