"""External editing preserves drafts, host locks and compact field geometry."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.layout import Layout

from hub.hub import install
from hub.config.general import GeneralSettings
from hub.locales import Language
from hub.ui.settings.editor import edit_text
from hub.widget.settings import SchemaForm
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_ish_integration import Prompt


class SettingsEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_editor_returns_to_the_same_field_and_resumes_keys(self):
        with tempfile.TemporaryDirectory() as directory, open("/dev/null", "w") as sink, create_pipe_input() as pipe:
            script = Path(directory) / "edit.py"
            script.write_text("from pathlib import Path\nimport sys\nPath(sys.argv[-1]).write_text('Updated\\n')\n")
            class EditorOutput(SizedOutput):
                def fileno(self):
                    return sink.fileno()
            prompt = Prompt(input=pipe, output=EditorOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            view.general = GeneralSettings(editor=shlex.join([sys.executable, str(script)]))
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("\x13")
                screen = view.settings
                await eventually(lambda: screen.page is not None and view.settings_open)
                await eventually(lambda: app.renderer._last_screen and any(
                    window.content is screen.global_list for window in app.renderer._last_screen.visible_windows))
                pipe.send_text("\t")
                await eventually(lambda: screen.page is not None and not screen.layout.sidebar_focused)
                field = screen.page.form.fields[("display_name",)]
                pipe.send_text("e")
                await eventually(lambda: field.input.text == "Updated" and not screen.busy)
                self.assertFalse(app._running_in_terminal)
                self.assertIs(app.layout.current_control, field.input.control)
                pipe.send_text("\x1b[B")
                await eventually(lambda: app.layout.current_control == screen.page.form.fields[("email",)].input.control)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_editor_process_argv_result_failure_and_cleanup(self):
        events = []
        @asynccontextmanager
        async def terminal():
            events.append("suspend")
            try:
                yield
            finally:
                events.append("restore")
        with tempfile.TemporaryDirectory() as directory, open("/dev/null", "r+") as tty:
            root = Path(directory)
            script = root / "editor with spaces.py"
            marker = root / "path.txt"
            script.write_text("import pathlib, sys\n"
                              "path = pathlib.Path(sys.argv[-1])\n"
                              "pathlib.Path(sys.argv[1]).write_text(str(path))\n"
                              "assert path.read_text() == 'before'\n"
                              "path.write_text('after\\nsecond\\n')\n"
                              "sys.exit(int(sys.argv[2]))\n")
            app = SimpleNamespace(input=tty, output=tty)
            with patch("hub.ui.settings.editor.get_app", return_value=app), \
                    patch("hub.ui.settings.editor.in_terminal", terminal):
                for code in (0, 7):
                    settings = GeneralSettings(editor=shlex.join([sys.executable, str(script), str(marker), str(code)]))
                    if code:
                        with self.assertRaises(subprocess.CalledProcessError) as failed:
                            await edit_text("before", settings, suffix=".json")
                        self.assertEqual(failed.exception.returncode, 7)
                    else:
                        self.assertEqual(await edit_text("before", settings), "after\nsecond")
                    self.assertFalse(Path(marker.read_text()).parent.exists())
            self.assertEqual(events, ["suspend", "restore"] * 2)

    async def test_e_uses_applied_editor_and_preserves_draft_on_failure(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("\x13")
                screen = view.settings
                await eventually(lambda: screen.page is not None and view.settings_open)
                await eventually(lambda: app.renderer._last_screen and any(
                    window.content is screen.global_list for window in app.renderer._last_screen.visible_windows))
                pipe.send_text("\t")
                await eventually(lambda: screen.page is not None and not screen.layout.sidebar_focused)
                field = screen.page.form.fields[("display_name",)]
                before = field.input.text
                applied = view.general = GeneralSettings(editor="nano -w")
                screen.preferences["general"]["editor"] = "unsaved-editor"
                with patch("hub.ui.settings.screen.edit_text", new=AsyncMock(return_value="first\nsecond\nthird\nfourth")) as editor:
                    pipe.send_text("\r")
                    await eventually(lambda: field.editing)
                    editor.assert_not_awaited()
                    self.assertEqual(field.input.text, before)
                    pipe.send_text("\x01\x0beE\x00second\x1b\rthird")
                    await eventually(lambda: field.input.text == "eE\nsecond\nthird")
                    self.assertTrue(field.editing)
                    pipe.send_text("\x1b[A")
                    await eventually(lambda: field.input.buffer.document.cursor_position_row == 1)
                    self.assertIs(app.layout.current_control, field.input.control)
                    pipe.send_text("\r")
                    await eventually(lambda: not field.editing)
                    editor.assert_not_awaited()
                    pipe.send_text("E")
                    await eventually(lambda: field.input.text.startswith("first") and not screen.busy)
                    editor.assert_awaited_once_with("eE\nsecond\nthird", applied, suffix=".txt")
                self.assertTrue(screen.page.dirty)
                self.assertEqual(screen.preferences["profile"]["display_name"], before)
                self.assertIs(app.layout.current_control, field.input.control)
                draft = field.input.text
                with patch("hub.ui.settings.screen.edit_text", new=AsyncMock(
                        side_effect=subprocess.CalledProcessError(9, ["editor"]))) as editor:
                    pipe.send_text("e")
                    await eventually(lambda: "9" in screen.status and not screen.busy)
                    self.assertEqual(field.input.text, draft)
                    self.assertFalse(field.editing)
                    field.readonly = True
                    pipe.send_text("e")
                    await asyncio.sleep(.05)
                    self.assertEqual(editor.await_count, 1)
                field.readonly = False
                pipe.send_text("\x1b[B")
                await eventually(lambda: app.layout.current_control == screen.page.form.fields[("email",)].input.control)
                self.assertEqual(field.input.text, draft)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_field_height_tracks_lines_and_hides_effective_values(self):
        form = SchemaForm({"type": "object", "properties": {"text": {"type": "string"}}},
                          {"text": "one"}, Language("ko"), effective={"engine": {
                              "values": {"text": "EFFECTIVE_SENTINEL"}, "sources": {"/text": "project"}}})
        field = form.fields[("text",)]
        app = Application(layout=Layout(form.container, focused_element=field.input),
                          input=DummyInput(), output=SizedOutput(), full_screen=True)
        with set_app(app):
            for text, height in (("one", 1), ("one\ntwo", 2), ("one\ntwo\nthree\nfour", 3), ("", 1)):
                field.input.text = text
                app.render_counter += 1
                app.renderer.render(app, app.layout)
                self.assertEqual(field.input.window.render_info.window_height, height)
                rendered = "\n".join("".join(cell.char for _, cell in sorted(row.items()))
                                     for row in app.renderer._last_screen.data_buffer.values())
                for hidden in ("저장값", "현재 적용값", "EFFECTIVE_SENTINEL"):
                    self.assertNotIn(hidden, rendered)
