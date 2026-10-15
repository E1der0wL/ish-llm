"""Completion overlays use the focused popup field's rendered cursor position."""

import asyncio
from pathlib import Path
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.menus import CompletionsMenuControl

from examples.hub.preview import install_preview
from hub.widget.forms.component_manager import ComponentManager
from hub.widget.forms.file_attachment import attach_file
from tests.hub.test_ish_integration import Prompt
from tests.hub.support import SizedOutput, eventually


class PopupCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_popup_paths_complete_at_cursor_inside_host_float_and_after_resize(self):
        with tempfile.TemporaryDirectory() as folder, create_pipe_input() as pipe:
            root = Path(folder)
            (root / "sample-directory").mkdir()
            (root / "sample-file.txt").write_text("example")
            size = SizedOutput()
            prompt = Prompt(input=pipe, output=size)
            installation = install_preview(prompt)
            view, app = installation.view, prompt.app
            view.file_root = root
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))

            def menus():
                screen = app.renderer._last_screen
                return [(window, rect) for window, rect in screen.visible_windows_to_write_positions.items()
                        if isinstance(window.content, CompletionsMenuControl)] if screen else []

            async def paint():
                ready = asyncio.Event()
                def done(_): ready.set()
                app.after_render += done
                try:
                    app.invalidate()
                    await asyncio.wait_for(ready.wait(), 3)
                finally:
                    app.after_render -= done

            try:
                await eventually(lambda: app.is_running and view.visible)
                for name in ("tools", "rag", "skills", "workflows", "agents", "mcp", "attach"):
                    with self.subTest(popup=name), set_app(app):
                        if name == "attach":
                            attach_file(view)
                            field = app.layout.current_control
                        else:
                            popup = ComponentManager(view, lambda *a: None, name=name,
                                                     directories=name == "tools")
                            view.dialogs.show(popup, popup.path)
                            field = popup.path.control
                        window = app.layout.current_window
                        pipe.send_text("sa")
                        await eventually(lambda: field.buffer.complete_state is not None and bool(menus()))
                        for columns, rows in ((132, 40), (80, 24)):
                            size.columns, size.rows = columns, rows
                            app._on_resize()
                            await paint()
                            screen = app.renderer._last_screen
                            self.assertEqual(len(menus()), 1)
                            _, rect = menus()[0]
                            cursor = screen.get_menu_position(window)
                            self.assertGreater(cursor.x, 0)
                            self.assertEqual(rect.xpos, min(cursor.x, columns - rect.width))
                            self.assertTrue(rect.ypos == cursor.y + 1 or rect.ypos + rect.height == cursor.y,
                                            (name, rect, cursor))
                        pipe.send_text("\x1b[B\t")
                        await eventually(lambda: field.buffer.text.startswith("sample-"))
                        self.assertIs(app.layout.current_control, field)
                        view.close_dialog()
                        await paint()
                        self.assertEqual(menus(), [])
                        self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                app.exit(result="")
                await task
                installation.close()
