"""Writable inputs share selection, clipboard and undo without touching shell/readers."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.controls import TextArea
from tests.hub.support import SizedOutput, eventually, open_settings


class TextEditingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.enterContext(patch('hub.widget.clipboard.system_clipboard', new=AsyncMock(return_value=None)))
        self.app, self.view, self.shell = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.composer.window.render_info)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.view.progress.close()

    async def send(self, keys):
        self.pipe.send_text(keys)
        await asyncio.sleep(.06)

    async def paint(self):
        painted = asyncio.Event()
        callback = lambda _: painted.set()
        self.app.after_render += callback
        try:
            self.app.invalidate()
            await asyncio.wait_for(painted.wait(), 3)
        finally:
            self.app.after_render -= callback

    async def test_composer_selection_replacement_cut_undo_redo_and_new_stop(self):
        buffer = self.view.composer.buffer
        self.view.on_interrupt = lambda session: calls.append(session)
        calls = []
        await self.send('hello world\x01')
        self.assertEqual(buffer.document.selection_range(), (0, 11))
        await self.send('\x18')
        self.assertEqual(buffer.text, '')
        self.assertEqual(self.app.clipboard.get_data().text, 'hello world')
        self.assertEqual(calls, [])
        await self.send('\x18')  # No selection: no deletion, no interruption.
        self.assertEqual(calls, [])
        await self.send('\x1a')
        self.assertEqual(buffer.text, 'hello world')
        await self.send('\x19')
        self.assertEqual(buffer.text, '')
        await self.send('\x16')
        self.assertEqual(buffer.text, 'hello world')
        await self.send('\x01한글')
        self.assertEqual(buffer.text, '한글')
        await self.send('\x01\x1b[3~')
        self.assertEqual(buffer.text, '')
        await self.send('remain\x17')
        self.assertEqual(buffer.text, 'remain')
        self.assertEqual(calls, [self.view.sessions[self.view.selected].id])

    async def test_shift_navigation_paste_and_dialog_input(self):
        buffer = self.view.composer.buffer
        await self.send('alpha beta\x1b[1;6D')  # Ctrl+Shift+Left selects the word.
        self.assertEqual(buffer.text[slice(*buffer.document.selection_range())], 'beta')
        await self.send('\x03')
        self.assertEqual(self.app.clipboard.get_data().text, 'beta')
        await self.send('\x1b[D')
        self.assertIsNone(buffer.selection_state)
        self.assertEqual(buffer.cursor_position, 6)
        await self.send('\x01\x1b[200~pasted\nsecond\x1b[201~')
        self.assertEqual(buffer.text, 'pasted\nsecond')
        await self.send('\x01\x16')
        self.assertEqual(buffer.text, 'beta')
        await self.send('\x1a')
        self.assertEqual(buffer.text, 'pasted\nsecond')
        await self.send('\x01\x00')
        self.assertEqual(buffer.text, '\n')
        with set_app(self.app):
            field = TextArea(text='old name', multiline=False)
            self.view.open_dialog('Name', field, focus=field)
        await self.send('\x01new name')
        self.assertEqual(field.text, 'new name')
        self.assertEqual(buffer.text, '\n')
        await self.send('\x01\x7f')
        self.assertEqual(field.text, '')

    async def test_settings_navigation_is_readonly_then_enter_enables_gui_editing(self):
        await open_settings(self.app, self.pipe, self.view)
        screen = self.view.settings
        with set_app(self.app):
            screen.choose('profile')
            field = screen.page.form.fields[('display_name',)]
            self.app.layout.focus(field.input)
        before = field.input.text
        await self.send('\x01\x18\x1a\x19')
        self.assertEqual(field.input.text, before)
        self.assertFalse(field.editing)
        await self.send('\r\x01new\x00name\r')
        self.assertFalse(field.editing)
        self.assertEqual(field.input.text, 'new\nname')

    async def test_mouse_selection_unicode_wrapping_and_typing_replaces_selection(self):
        buffer = self.view.composer.buffer
        buffer.text = '한글 abc def'
        buffer.cursor_position = 0
        await self.paint()
        rect = self.app.renderer._last_screen.visible_windows_to_write_positions[self.view.composer.window]
        y = rect.ypos
        x = next(x for x, cell in self.app.renderer._last_screen.data_buffer[y].items() if cell.char == '한')
        await self.send(f'\x1b[<0;{x+1};{y+1}M\x1b[<32;{x+5};{y+1}M\x1b[<0;{x+5};{y+1}m')
        self.assertEqual(buffer.document.selection_range(), (0, 2))
        await self.send('새')
        self.assertEqual(buffer.text, '새 abc def')
        # A drag outside the input must not activate the underlying panel.
        buffer.text = 'a' * 170
        buffer.cursor_position = 0
        await self.paint()
        rect = self.app.renderer._last_screen.visible_windows_to_write_positions[self.view.composer.window]
        await self.send(f'\x1b[<0;{rect.xpos+1};{rect.ypos+1}M')
        await self.send(f'\x1b[<32;{rect.xpos+3};{rect.ypos+2}M')
        self.assertGreater(buffer.cursor_position, 10)
        await self.send(f'\x1b[<32;1;{rect.ypos+2}M\x1b[<0;1;{rect.ypos+2}m')
        self.assertIs(self.app.layout.current_control, self.view.composer.control)
        self.assertIsNotNone(buffer.selection_state)

    async def test_minimized_shell_keeps_its_original_keymap(self):
        await self.send('\x11')
        self.assertFalse(self.view.visible)
        await self.send('shell\x01')
        self.assertEqual(self.shell.buffer.cursor_position, 0)
        self.assertIsNone(self.shell.buffer.selection_state)
