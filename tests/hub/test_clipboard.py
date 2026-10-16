"""System clipboard transport and Hub-only register ownership."""

import asyncio
import base64
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.clipboard import ClipboardData, InMemoryClipboard
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.clipboard import SystemClipboard, copy_text, export_text, system_clipboard, terminal_clipboard_supported
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
        native = SimpleNamespace(transfer=AsyncMock())
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=True), \
             patch("shutil.which", return_value="/windows/clip.exe"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.assertTrue(await system_clipboard("한글 😀", x11=native))
            self.assertEqual(process.communicate.call_args.args[0], "한글 😀".encode("utf-16-le"))
            self.assertEqual(spawn.call_args.args, ("/windows/clip.exe",))
            native.transfer.assert_not_awaited()
            process.communicate.return_value = ("Windows 한글\r\n".encode(), b"")
            self.assertEqual(await system_clipboard(x11=native), "Windows 한글\n")
            self.assertIn("Get-Clipboard -Raw", spawn.call_args.args[-1])
            native.transfer.assert_not_awaited()
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, clear=True), \
             patch("shutil.which", side_effect=lambda name, **kwargs: "/bin/" + name), \
             patch("asyncio.create_subprocess_exec", side_effect=[OSError(), process]) as spawn:
            process.communicate.return_value = ("붙여넣기\r\n".encode(), b"")
            self.assertEqual(await system_clipboard(), "붙여넣기\n")
            self.assertEqual(spawn.call_args.args, ("/bin/xclip", "-selection", "clipboard", "-o"))

    async def test_native_x11_fallback_respects_shell_and_explicit_bridge(self):
        native = SimpleNamespace(transfer=AsyncMock(return_value="external"))
        env = {"DISPLAY": ":25", "XAUTHORITY": "/session/auth", "SSH_TTY": "/dev/pts/1"}
        with patch("shutil.which", return_value=None):
            self.assertEqual(await system_clipboard(env=env, x11=native), "external")
            native.transfer.assert_awaited_once_with(None, env)
            native.transfer.reset_mock()
            self.assertIsNone(await system_clipboard(env={**env, "HUB_CLIPBOARD_PASTE": "custom"}, x11=native))
            self.assertIsNone(await system_clipboard(env={"SSH_TTY": "ssh"}, x11=native))
            native.transfer.assert_not_awaited()

    async def test_shell_environment_drives_helper_lookup_and_execution(self):
        from examples.hub.preview import install_preview
        from tests.hub.test_ish_integration import Prompt
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            prompt.context = SimpleNamespace(environ={"PATH": "/desktop/bin", "DISPLAY": ":7"})
            installation = install_preview(prompt)
            try:
                environment = installation.view.clipboard_environment
                self.assertEqual(environment(), prompt.context.environ)
                prompt.context.environ["DISPLAY"] = ":8"
                process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"external", b"")))
                with patch("shutil.which", return_value="/desktop/bin/xclip") as which, \
                     patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
                    self.assertEqual(await system_clipboard(env=environment()), "external")
                which.assert_called_once_with("xclip", path="/desktop/bin")
                self.assertEqual(spawn.call_args.kwargs["env"], {"PATH": "/desktop/bin", "DISPLAY": ":8"})
                self.assertNotEqual(os.environ.get("DISPLAY"), ":8")
            finally:
                installation.close()

    async def test_ssh_uses_forwarded_x11_not_remote_desktop_clipboard(self):
        env = {"SSH_CONNECTION": "client 1234 server 22", "DISPLAY": "localhost:10.0",
               "WAYLAND_DISPLAY": "wayland-0", "WSL_DISTRO_NAME": "Ubuntu",
               "XAUTHORITY": "/home/user/.Xauthority", "PATH": "/usr/bin"}
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"forwarded", b"")))
        with patch("shutil.which", side_effect=lambda name, **kwargs: "/usr/bin/" + name) as which, \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.assertEqual(await system_clipboard(env=env), "forwarded")
            which.assert_called_once_with("xclip", path="/usr/bin")
            self.assertEqual(spawn.call_args.kwargs["env"], env)
            which.reset_mock()
            env.pop("DISPLAY")
            self.assertIsNone(await system_clipboard(env=env))
            which.assert_not_called()

    async def test_custom_bridge_roundtrip_waits_for_copy_without_blocking_ui(self):
        # A real subprocess verifies UTF-8 stdin/stdout, quoted paths, a cold
        # service taking over one second, and Ctrl+V waiting for pending export.
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "clipboard bridge.py"
            storage = Path(directory) / "clipboard.txt"
            helper.write_text("import pathlib, sys, time\n"
                              "path = pathlib.Path(sys.argv[2])\n"
                              "if sys.argv[1] == 'copy':\n"
                              "    time.sleep(1.1)\n"
                              "    path.write_bytes(sys.stdin.buffer.read())\n"
                              "else:\n"
                              "    sys.stdout.buffer.write(path.read_bytes())\n")
            env = dict(os.environ,
                       HUB_CLIPBOARD_COPY=shlex.join([sys.executable, str(helper), "copy", str(storage)]),
                       HUB_CLIPBOARD_PASTE=shlex.join([sys.executable, str(helper), "paste", str(storage)]))
            app = SimpleNamespace(is_running=True, create_background_task=asyncio.create_task)
            app.clipboard = register = SystemClipboard(app, InMemoryClipboard(), environment=lambda: env)
            with set_app(app):
                copying = asyncio.create_task(copy_text("한글 😀\n$(literal)"))
                await asyncio.sleep(.05)
                self.assertFalse(copying.done())
                self.assertEqual(register.get_data().text, "한글 😀\n$(literal)")
                data = await register.read()
                self.assertEqual(await copying, "clipboard_copied")
            self.assertEqual(data.text, "한글 😀\n$(literal)")
            storage.write_text("외부에서 복사", encoding="utf-8")
            self.assertEqual((await register.read()).text, "외부에서 복사")
            storage.write_text("")
            self.assertEqual((await register.read()).text, "")
            self.assertEqual(register.get_data().text, "")

    async def test_failed_custom_bridge_is_authoritative(self):
        env = {"HUB_CLIPBOARD_COPY": "missing-copy --clipboard", "HUB_CLIPBOARD_PASTE": "missing-paste",
               "DISPLAY": ":0", "HUB_OSC52": "1"}
        app = SimpleNamespace(is_running=True, create_background_task=asyncio.create_task, output=Mock())
        register = SystemClipboard(app, InMemoryClipboard(), environment=lambda: env)
        with patch("shutil.which", return_value=None) as which:
            register.set_data(ClipboardData("Hub text"))
            self.assertEqual(await register.pending, "clipboard_internal")
            self.assertIsNone(await register.read())
            self.assertEqual(register.get_data().text, "Hub text")
        self.assertEqual([call.args[0] for call in which.call_args_list], ["missing-copy", "missing-paste"])
        app.output.write_raw.assert_not_called()

    async def test_terminal_paste_supersedes_slow_external_read_without_export(self):
        app = SimpleNamespace(is_running=True, create_background_task=asyncio.create_task)
        register = SystemClipboard(app, InMemoryClipboard())
        started, release = asyncio.Event(), asyncio.Event()
        async def slow_read(**kwargs):
            started.set()
            await release.wait()
            return "stale external"
        with patch("hub.widget.clipboard.system_clipboard", side_effect=slow_read), \
             patch("hub.widget.clipboard.export_text", new=AsyncMock()) as export:
            reading = asyncio.create_task(register.read())
            await started.wait()
            register.remember(ClipboardData("new terminal paste"))
            release.set()
            self.assertIsNone(await reading)
        self.assertEqual(register.get_data().text, "new terminal paste")
        export.assert_not_awaited()

    async def test_copies_finish_in_order(self):
        app = SimpleNamespace(is_running=True, create_background_task=asyncio.create_task)
        register = SystemClipboard(app, InMemoryClipboard())
        started, release = asyncio.Event(), asyncio.Event()
        writes = []
        async def write(app, text, **kwargs):
            if text == "first":
                started.set()
                await release.wait()
            writes.append(text)
            return "clipboard_copied"
        with patch("hub.widget.clipboard.export_text", side_effect=write):
            register.set_data(ClipboardData("first"))
            await started.wait()
            register.set_data(ClipboardData("last"))
            release.set()
            await register.pending
        self.assertEqual(writes, ["first", "last"])
        self.assertEqual(register.get_data().text, "last")

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
                with patch("hub.widget.clipboard.system_clipboard", new=AsyncMock(return_value=None)) as read:
                    pipe.send_text("\x16")
                    await eventually(lambda: read.await_count == 1)
                    await asyncio.sleep(.05)
                    self.assertEqual(view.composer.text, "")
                    self.assertEqual(app.clipboard.get_data().text, "selected")
                    self.assertFalse(view.toasts.items)
                with patch("hub.widget.clipboard.export_text", new=AsyncMock(return_value="clipboard_internal")):
                    app.clipboard.set_data(ClipboardData("internal only"))
                    await app.clipboard.pending
                    self.assertFalse(view.toasts.items)
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
