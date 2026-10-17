"""Shared profile navigation and current-project-only settings transactions."""

import asyncio
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig
from hub.config.preferences import PreferencesStore
from hub.ui.application import create_application
from tests.hub.support import SizedOutput, eventually, settings_button, open_home
from tests.hub.test_live import until


class ProjectSettingsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_project_selection_scoped_settings_and_stay_on_save_cancel(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            output = SizedOutput()
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe, output=output)
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and view.projects.loaded and not view.projects.busy)
                original = view.project_id
                pipe.send_text("\x10")
                await eventually(lambda: app.layout.current_control is view.profile_bar.page.control)
                for width, height in ((132, 40), (80, 24)):
                    output.columns, output.rows = width, height
                    app._on_resize()
                    await asyncio.sleep(.1)
                    panel = view._sidebar.content
                    self.assertEqual(panel.sections[0].height.preferred, round((height - 2) * .27))
                    position = app.renderer._last_screen.visible_windows_to_write_positions[view.profile_bar.presets.button.window]
                    self.assertEqual(position.ypos, height - 3)
                    self.assertLessEqual(position.xpos + position.width, view.sidebar_width())
                pipe.send_text("\r")
                await until(lambda: view.settings_open and screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("profile")
                    self.assertEqual(screen.page_title(), "전역 설정 • 프로필")
                    screen.page.form.fields[("display_name",)].input.text = "Nickname"
                settings_button(app, pipe, view)
                await until(lambda: view.user_profile.display_name == "Nickname" and not screen.busy)
                self.assertTrue(view.settings_open)
                self.assertIn("Nickname • ", view.profile_bar.identity()[0][1])
                self.assertEqual(PreferencesStore(directory).load()["profile"]["display_name"], "Nickname")
                with set_app(app):
                    screen.choose("engine:loop")
                    self.assertEqual(screen.page_title(), "프로젝트 • 엔진 · loop")
                    project_page = screen.page
                    loop_field = next(iter(project_page.engine_forms["loop"].fields.values()))
                    screen.choose("component:tools")
                    self.assertEqual(screen.page_title(), "프로젝트 • 컴포넌트 · tools")
                    self.assertIs(screen.page, project_page)
                    self.assertNotIn(loop_field, screen.page.fields())
                    screen.choose("project:general")
                    self.assertEqual(screen.page_title(), "프로젝트 • 일반")
                    screen.page.form.fields[("title",)].input.text = "Unsaved"
                settings_button(app, pipe, view, "cancel")
                await until(lambda: screen.page.form.fields[("title",)].input.text != "Unsaved")
                self.assertTrue(view.settings_open)
                await open_home(app, view)
                # Padding in the shared profile rectangle must also focus its buttons.
                output.columns = 132
                app._on_resize()
                await asyncio.sleep(.1)
                pipe.send_text(f"\x1b[<0;2;{output.rows - 3}M\x1b[<0;2;{output.rows - 3}m")
                await eventually(lambda: app.layout.current_control in view.profile_bar.controls)
                with set_app(app):
                    view.settings.set_sidebar_width(1)
                self.assertGreaterEqual(view.sidebar_width(), view.profile_bar.minimum_width)
                with set_app(app):
                    app.layout.focus(view.projects.control)
                pipe.send_text("cSecond project\r")
                await until(lambda: view.project_id != original and not view.projects.busy)
                second = view.project_id
                self.assertEqual(view.project_title, "Second project")
                with set_app(app):
                    view.open_page(True)
                await until(lambda: not screen.busy)
                with set_app(app):
                    screen.choose("project:general")
                    screen.filter("missing-setting")
                    screen.choose(original)
                self.assertEqual(screen.page.identifier, second)
                self.assertNotIn(original, screen.navigation.left_items())
                await open_home(app, view)
                with set_app(app):
                    view.projects.select(original)
                await until(lambda: view.project_id == original and not view.projects.busy)
                self.assertEqual(screen.query, "missing-setting")
                with set_app(app):
                    view.open_page(True)
                await until(lambda: not screen.busy)
                with set_app(app):
                    screen.choose("project:general")
                self.assertEqual(screen.page.identifier, original)
            finally:
                app.exit()
                await task
                await controller.aclose()
