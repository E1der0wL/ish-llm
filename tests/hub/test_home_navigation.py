"""Home title, category-only Tab navigation and the shared profile footer."""

import asyncio
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from tests.hub.support import SizedOutput, eventually


class HomeNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_categories_profile_buttons_and_shell_restore(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, shell = create_preview(input=pipe, output=output)
            task = asyncio.create_task(app.run_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("shell draft\x11")
                await eventually(lambda: view.visible)
                view.project_title = "Renamed project"
                self.assertEqual(view.header.fragments()[0][1], "   홈")
                view.project_id = "p"
                view.projects.selected = "p"
                view.projects.items = [{"id": "p", "title": "Project"}]
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control is view._session_control)
                pipe.send_text("\x1b[A")
                await asyncio.sleep(.06)
                self.assertIs(app.layout.current_control, view._session_control)
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control is view.projects.control)
                pipe.send_text("\x1b[A\x1b[B")
                await asyncio.sleep(.06)
                self.assertIs(app.layout.current_control, view.projects.control)
                pipe.send_text("\x1b[Z")
                await eventually(lambda: app.layout.current_control is view._session_control)
                pipe.send_text("\x1b[C\x10")
                await eventually(lambda: app.layout.current_control is view.profile_bar.page.control)
                self.assertEqual(view.profile_bar.page.text, "설정")
                for sequence in ("\t", "\x1b[Z", "\x1b[A", "\x1b[B"):
                    pipe.send_text(sequence)
                    await asyncio.sleep(.05)
                    self.assertIs(app.layout.current_control, view.profile_bar.page.control)
                for width in (19, 24, 39):
                    with set_app(app):
                        view.settings.set_sidebar_width(width)
                    app.invalidate()
                    await asyncio.sleep(.08)
                    positions = app.renderer._last_screen.visible_windows_to_write_positions
                    left = positions[view.profile_bar.page.window]
                    right = positions[view.profile_bar.minimize.window]
                    edge = positions[view.chat_page.edge].xpos
                    self.assertLessEqual(abs(left.xpos - (edge - view.sidebar_width()) -
                                             (edge - right.xpos - right.width)), 1)
                pipe.send_text(" ")
                await eventually(lambda: view.settings_open)
                self.assertEqual(view.profile_bar.page.text, "홈")
                with set_app(app):
                    view.settings.choose("general")
                    app.layout.focus(view.settings.global_list)
                pipe.send_text("\x1b[B")
                await asyncio.sleep(.08)
                self.assertIs(app.layout.current_control, view.settings.global_list)
                self.assertEqual(view.settings.selected, "general")
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control is view.settings.project_list)
                pipe.send_text("\x1b[A")
                await asyncio.sleep(.08)
                self.assertIs(app.layout.current_control, view.settings.project_list)
                pipe.send_text("\x1b[Z")
                await eventually(lambda: app.layout.current_control is view.settings.global_list)
                pipe.send_text("\x10\r")
                await eventually(lambda: not view.settings_open)
                pipe.send_text("\x10\x1b[C")
                await eventually(lambda: app.layout.current_control is view.profile_bar.minimize.control)
                pipe.send_text("\t")
                await asyncio.sleep(.05)
                self.assertIs(app.layout.current_control, view.profile_bar.minimize.control)
                pipe.send_text("\x1b[D")
                await eventually(lambda: app.layout.current_control is view.profile_bar.page.control)
                pipe.send_text("\x1b[C\r")
                await eventually(lambda: not view.visible)
                self.assertIs(app.layout.current_control, shell.control)
                self.assertEqual(shell.text, "shell draft")
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()
