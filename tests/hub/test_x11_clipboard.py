"""Native clipboard interoperability on a private X server, never the desktop.

Set HUB_TEST_XVFB/HUB_TEST_XCLIP to test-only binaries if not in PATH.
"""

import asyncio
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.widget.clipboard import export_text, system_clipboard
from hub.widget.x11_clipboard import X11Clipboard
from hub.widget import _x11_selection
from tests.hub.support import SizedOutput, eventually


XVFB = os.environ.get("HUB_TEST_XVFB") or shutil.which("Xvfb")
XCLIP = os.environ.get("HUB_TEST_XCLIP") or shutil.which("xclip")


@unittest.skipUnless(XVFB and XCLIP, "Private X11 interoperability needs Xvfb and xclip")
class NativeClipboardTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = tempfile.TemporaryFile()
        cls.server = subprocess.Popen([XVFB, "-displayfd", "1", "-screen", "0", "640x480x24", "-nolisten", "tcp", "-noreset"],
                                      stdout=subprocess.PIPE, stderr=cls.log)
        cls.addClassCleanup(cls.stop_server)
        if not select.select([cls.server.stdout], [], [], 5)[0]:
            raise RuntimeError("Private X server did not start")
        number = cls.server.stdout.readline().decode().strip()
        if not number.isdigit():
            cls.log.seek(0)
            raise RuntimeError(cls.log.read().decode())
        cls.env = {key: value for key, value in os.environ.items()
                   if key not in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "WSL_DISTRO_NAME", "WSL_INTEROP",
                                  "HUB_CLIPBOARD_COPY", "HUB_CLIPBOARD_PASTE")}
        cls.env.update(DISPLAY=":" + number, PATH="/no-clipboard-helpers", SSH_TTY="test")

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)
        cls.server.stdout.close()
        cls.log.close()

    async def asyncSetUp(self):
        self.native = X11Clipboard()
        self.external = []

    async def asyncTearDown(self):
        await self.native.aclose()
        for process in self.external:
            if process.returncode is None:
                process.terminate()
            await process.wait()

    async def read_external(self, target="UTF8_STRING"):
        process = await asyncio.create_subprocess_exec(XCLIP, "-selection", "clipboard", "-o", "-target", target,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self.env)
        async with asyncio.timeout(5):
            text, error = await process.communicate()
        self.assertEqual(process.returncode, 0, error)
        return text

    async def write_external(self, text, target="UTF8_STRING"):
        process = await asyncio.create_subprocess_exec(XCLIP, "-selection", "clipboard", "-i", "-quiet", "-target", target,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, env=self.env)
        self.external.append(process)
        process.stdin.write(text)
        await process.stdin.drain()
        process.stdin.close()
        # Establish ownership before checking the native read (which must not
        # be satisfied by an earlier selection).
        async with asyncio.timeout(5):
            while True:
                reader = await asyncio.create_subprocess_exec(XCLIP, "-selection", "clipboard", "-o", "-target", target,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=self.env)
                output, _ = await reader.communicate()
                if output == text and reader.returncode == 0:
                    break
                await asyncio.sleep(.01)

    async def test_helperless_unicode_roundtrip_and_ownership_replacement(self):
        app = SimpleNamespace(output=Mock())
        for text in ("한글 😀\nsecond line", "", "replacement"):
            self.assertEqual(await export_text(app, text, env=self.env, x11=self.native), "clipboard_copied")
            self.assertEqual((await self.read_external()).decode(), text)
            self.assertEqual(await system_clipboard(env=self.env, x11=self.native), text)
        app.output.write_raw.assert_not_called()
        await self.write_external("external 한글".encode())
        self.assertEqual(await system_clipboard(env=self.env, x11=self.native), "external 한글")
        await self.write_external(b"")
        self.assertEqual(await system_clipboard(env=self.env, x11=self.native), "")

    async def test_large_incremental_transfers_both_directions(self):
        text = "한글 😀\n" * 150000
        self.assertTrue(await self.native.transfer(text, self.env))
        self.assertEqual((await self.read_external()).decode(), text)
        other = text + "external"
        await self.write_external(other.encode())
        self.assertEqual(await self.native.transfer(None, self.env), other)

    async def test_repeated_ownership_changes_do_not_miss_queued_events(self):
        for index in range(32):
            text = f"copy {index} 한글"
            self.assertTrue(await self.native.transfer(text, self.env), (index, self.native.error))
            self.assertEqual((await self.read_external()).decode(), text)

    async def test_targets_legacy_encoding_and_owner_cleanup(self):
        self.assertTrue(await self.native.transfer("café", self.env))
        targets = (await self.read_external("TARGETS")).decode()
        self.assertIn("UTF8_STRING", targets)
        self.assertIn("TIMESTAMP", targets)
        self.assertIn("MULTIPLE", targets)
        self.assertEqual(await self.read_external("STRING"), b"caf\xe9")
        await self.write_external(b"caf\xe9", target="STRING")
        self.assertEqual(await self.native.transfer(None, self.env), "café")
        self.assertTrue(await self.native.transfer("after minimize", self.env))
        owner = self.native.owner
        await asyncio.sleep(.05)
        self.assertEqual(await self.read_external(), b"after minimize")
        await self.native.aclose()
        self.assertIsNotNone(owner.returncode)

    async def test_failed_display_does_not_destroy_previous_copy_or_block_loop(self):
        self.assertTrue(await self.native.transfer("keep", self.env))
        owner = self.native.owner
        task = asyncio.create_task(self.native.transfer("lost", {**self.env, "DISPLAY": ":65530"}))
        ticks = 0
        while not task.done():
            ticks += 1
            await asyncio.sleep(.001)
        self.assertIsNone(await task)
        self.assertEqual(self.native.error, "clipboard_x11_connection")
        self.assertGreater(ticks, 0)
        self.assertIs(self.native.owner, owner)
        self.assertEqual(await self.read_external(), b"keep")

    async def test_uninstalled_forwarded_locale_keeps_utf8_clipboard_working(self):
        env = {**self.env, "LC_ALL": "hub_missing_locale.UTF-8"}
        self.assertTrue(await self.native.transfer("원격 환경 😀", env), self.native.error)
        self.assertEqual((await self.read_external()).decode(), "원격 환경 😀")
        self.assertEqual(await self.native.transfer(None, env), "원격 환경 😀")

    async def test_hub_selection_keys_minimize_and_reopen(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.clipboard_environment = lambda: dict(self.env)
            original = app.clipboard
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                view.composer.text = "마우스 선택 😀"
                buffer = view.composer.buffer
                buffer.cursor_position = 0
                buffer.start_selection()
                buffer.cursor_position = len(buffer.text)
                pipe.send_text("\x03")
                await eventually(lambda: app.clipboard.pending is not None)
                self.assertEqual(await app.clipboard.pending, "clipboard_copied")
                self.assertEqual((await self.read_external()).decode(), buffer.text)
                self.assertIsNotNone(buffer.selection_state)
                with set_app(app):
                    view.hide(app)
                self.assertIs(app.clipboard, original)
                self.assertEqual((await self.read_external()).decode(), buffer.text)
                with set_app(app):
                    view.show(app)
                buffer.exit_selection()
                view.composer.text = ""
                await self.write_external("외부에서 복사".encode())
                pipe.send_text("\x16")
                await eventually(lambda: view.composer.text == "외부에서 복사")
                # Terminal bracketed paste stays independent of clipboard reads.
                view.composer.text = ""
                pipe.send_text("\x1b[200~터미널 붙여넣기\x1b[201~")
                await eventually(lambda: view.composer.text == "터미널 붙여넣기")
                self.assertEqual((await self.read_external()).decode(), "외부에서 복사")
            finally:
                view.close()
                await view.native_clipboard.aclose()
                app.exit()
                await task

    async def test_host_death_releases_native_owner(self):
        script = (
            "import asyncio, os\n"
            "from hub.widget.x11_clipboard import X11Clipboard\n"
            "async def main():\n"
            "    clipboard = X11Clipboard()\n"
            "    assert await clipboard.transfer('owned', dict(os.environ))\n"
            "    print(clipboard.owner.pid, flush=True)\n"
            "    await asyncio.Future()\n"
            "asyncio.run(main())\n"
        )
        host = await asyncio.create_subprocess_exec(sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=self.env)
        try:
            async with asyncio.timeout(5):
                pid = int(await host.stdout.readline())
            self.assertEqual(await self.read_external(), b"owned")
            host.kill()
            await host.wait()
            status = Path(f"/proc/{pid}/status")
            async with asyncio.timeout(5):
                while status.exists():
                    try:
                        if "State:\tZ" in status.read_text():
                            break
                    except (FileNotFoundError, ProcessLookupError):
                        break
                    await asyncio.sleep(.01)
        finally:
            if host.returncode is None:
                host.kill()
            await host.wait()

    async def test_abandoned_copy_does_not_replace_clipboard_with_empty_text(self):
        await self.write_external(b"keep external")
        for payload in (b"", b"\0\0", (50).to_bytes(8, "big") + b"partial"):
            process = await asyncio.create_subprocess_exec(sys.executable, _x11_selection.__file__, "copy",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self.env)
            async with asyncio.timeout(5):
                output, _ = await process.communicate(payload)
            self.assertNotEqual(output, b"owned\n")
            self.assertEqual(await self.read_external(), b"keep external")
