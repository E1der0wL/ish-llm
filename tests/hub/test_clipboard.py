"""System clipboard transport and Hub-only register ownership."""

import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.clipboard import SystemClipboard, export_text, system_clipboard, terminal_clipboard_supported
from prompt_toolkit.output.vt100 import Vt100_Output
from tests.hub.support import SizedOutput, eventually


class ClipboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_osc52_requires_known_real_terminal_and_bounds_payload(self):
        output = Mock(spec=Vt100_Output)
        output.stdout = SimpleNamespace(isatty=lambda: True)
        app = SimpleNamespace(output=output)
        with patch('hub.widget.clipboard.system_clipboard', new=AsyncMock(return_value=None)):
            with patch.dict('os.environ', {'TERM': 'xterm-256color'}, clear=True):
                self.assertEqual(await export_text(app, 'text'), 'clipboard_internal')
                output.write_raw.assert_not_called()
            with patch.dict('os.environ', {'TERM': 'xterm-kitty'}, clear=True):
                self.assertTrue(terminal_clipboard_supported(app))
                self.assertEqual(await export_text(app, 'copy'), 'clipboard_terminal')
                output.write_raw.assert_called_once_with('\x1b]52;c;' + base64.b64encode(b'copy').decode() + '\x07')
                output.reset_mock()
                self.assertEqual(await export_text(app, 'x' * 6000), 'clipboard_internal')
                output.write_raw.assert_not_called()
            with patch.dict('os.environ', {'TERM': 'xterm-kitty', 'TMUX': '1'}, clear=True):
                self.assertFalse(terminal_clipboard_supported(app))
            with patch.dict('os.environ', {'TERM': 'xterm-kitty', 'HUB_OSC52': '0'}, clear=True):
                self.assertFalse(terminal_clipboard_supported(app))

    async def test_wsl_unicode_and_linux_fallback(self):
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True), \
             patch("shutil.which", return_value="/windows/clip.exe"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.assertTrue(await system_clipboard("한글 😀"))
            self.assertEqual(process.communicate.call_args.args[0], "한글 😀".encode("utf-16-le"))
            self.assertEqual(spawn.call_args.args, ("/windows/clip.exe",))
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, clear=True), \
             patch("shutil.which", side_effect=lambda name: "/bin/" + name), \
             patch("asyncio.create_subprocess_exec", side_effect=[OSError(), process]) as spawn:
            process.communicate.return_value = ("붙여넣기\r\n".encode(), b"")
            self.assertEqual(await system_clipboard(), "붙여넣기\n")
            self.assertEqual(spawn.call_args.args, ("/bin/xclip", "-selection", "clipboard", "-o"))

    async def test_vm_terminal_fallback_and_bounded_process_cleanup(self):
        output = Mock()
        with patch("hub.widget.clipboard.system_clipboard", new=AsyncMock(return_value=None)), \
             patch.dict("os.environ", {}, clear=True):
            result = await export_text(SimpleNamespace(output=output), "copy")
        self.assertEqual(result, "clipboard_internal")
        output.write_raw.assert_not_called()
        process = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=TimeoutError),
                                  kill=Mock(), wait=AsyncMock())
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland"}, clear=True), \
             patch("shutil.which", return_value="/bin/wl-copy"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            self.assertIsNone(await system_clipboard("test"))
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()

    async def test_copy_paste_selection_and_restore_shell_register(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            original = app.clipboard
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                self.assertIsInstance(app.clipboard, SystemClipboard)
                with patch("hub.widget.clipboard.export_text", new=AsyncMock(return_value="clipboard_copied")) as copied:
                    view.composer.text = "selected text"
                    buffer = view.composer.buffer
                    buffer.cursor_position = 0
                    buffer.start_selection()
                    buffer.cursor_position = 8
                    pipe.send_text("\x03")
                    await eventually(lambda: copied.await_count == 1)
                    self.assertEqual(copied.call_args.args[1], "selected")
                    self.assertTrue(view.visible)
                buffer.exit_selection()
                view.composer.text = ""
                with patch("hub.widget.clipboard.system_clipboard", new=AsyncMock(return_value="시스템 값")):
                    pipe.send_text("\x16")
                    await eventually(lambda: view.composer.text == "시스템 값")
                with set_app(app):
                    view.hide(app)
                self.assertIs(app.clipboard, original)
            finally:
                app.exit()
                await task
                view.render_worker.close()
