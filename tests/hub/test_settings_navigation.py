"""Settings drafts, region navigation, and atomic project configuration saves."""

import asyncio
from copy import deepcopy
from functools import partial
import tempfile
import unittest
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.application.current import set_app

from hub.ui.application import create_application
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.worker import BackendWorker
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput
from tests.hub.test_settings import settings_backend


class SettingsDraftTests(unittest.IsolatedAsyncioTestCase):
    async def test_sidebar_selects_on_navigation_and_last_selection_wins(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text("\x13")
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("profile")
                    screen.switch_panel()
                profile = screen.page
                profile.form.fields[("email",)].input.text = "draft@example.com"
                for name in ("appearance", "general", view.project_id):
                    pipe.send_text("\x1b[B")
                    await until(lambda: screen.selected == name and not screen.busy)
                self.assertIs(app.layout.current_control, screen.project_list)
                record = screen.page.record
                screen.pages.pop(view.project_id)
                with set_app(app):
                    screen.choose("general")
                requests = []
                with patch.object(screen, "request", side_effect=lambda op, args, done: requests.append(done)):
                    pipe.send_text("\x1b[B")
                    await until(lambda: screen.busy and bool(requests))
                    pipe.send_text("\x1b[A")
                    await until(lambda: screen._left_key == "general")
                    with set_app(app):
                        requests[-1](record)
                    self.assertEqual(screen.page.name, "general")
                    self.assertEqual(screen.selected, "general")
                pipe.send_text("\x1b[A\x1b[A")
                await until(lambda: screen.selected == "profile")
                self.assertIs(screen.page, profile)
                self.assertEqual(profile.form.fields[("email",)].input.text, "draft@example.com")
                self.assertIs(type(view._session_control), type(screen.global_list))
                self.assertIs(type(screen.global_list), type(screen.project_list))
                pipe.send_text("\x1b[B\x1b[B\x1b[B")
                await until(lambda: screen.selected == view.project_id)
                pipe.send_text("c")
                await until(lambda: screen.selected == "new")
                pipe.send_text("\x1b")
                await until(lambda: app.layout.current_control == screen.project_list)
                pipe.send_text("\x1b[A")
                await until(lambda: screen.selected == "general")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_selection_and_configuration_publish_together(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, "loop", "test/model", auto_title=False), settings_backend)
            await runtime.start()
            if not runtime.sessions:
                await runtime.new_session()
            try:
                project = runtime.project
                await project.components.aselect(["tools"])
                original = await runtime.settings_service.load_project(project.id)
                values = deepcopy(original["values"])
                values["components"] = ["tools", "demo"]
                values["config"]["parameters"].setdefault("components", {})["demo"] = {"config": {"count": 3}}
                component = runtime.backend.project_manager.components.get("demo")
                with patch.object(component, "initialize", side_effect=RuntimeError("initialization failed")):
                    with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                        await runtime.settings_service.save_project(project.id, values,
                            original["config_version"], ["tools"])
                after = await project.aget_data()
                self.assertEqual(after.components, ("tools",))
                self.assertEqual(after.config.to_dict(), original["project"]["config"])
                await runtime.settings_service.save_project(project.id, values,
                    original["config_version"], ["tools"])
                saved = await project.aget_data()
                self.assertEqual(saved.components, ("tools", "demo"))
                self.assertEqual(saved.config.parameters.setdefault("components", {})["demo"]["config"]["count"], 3)
                with self.assertRaises(ValueError):
                    await runtime.settings_service.save_project(project.id, values,
                        original["config_version"], ["tools"])
            finally:
                await runtime.close()

    async def test_keyboard_regions_editing_dirty_cancel_and_width(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                pipe.send_text("\x13")
                await until(lambda: screen.page is not None and not screen.busy)
                with set_app(app):
                    screen.choose("profile")
                    screen.switch_panel()
                self.assertIs(app.layout.current_control, screen.global_list)
                pipe.send_text("\t")
                field = screen.page.form.fields[("display_name",)]
                await until(lambda: app.layout.current_control == field.input.control)
                before = field.input.text
                pipe.send_text("\rchanged\r")
                await until(lambda: field.input.text != before and not field.editing)
                self.assertTrue(screen.page.dirty)
                self.assertIn("*", "".join(part[1] for part in screen.page.save._get_text_fragments()))
                pipe.send_text("\t")
                await until(lambda: app.layout.current_control == screen.page.save.control)
                pipe.send_text("\t")
                await until(lambda: app.layout.current_control == field.input.control)
                pipe.send_text("\x1b[Z")
                await until(lambda: app.layout.current_control == screen.page.save.control)
                screen.cancel(screen.page)
                self.assertFalse(screen.page.dirty)
                self.assertEqual(screen.page.form.fields[("display_name",)].input.text, before)
                app.layout.focus(screen.global_list)
                width = view.theme.sidebar_width
                pipe.send_text("\x1b[1;5C")
                await until(lambda: view.theme.sidebar_width == width + 1)
                self.assertTrue(screen.pages["appearance"].dirty)
                self.assertEqual(view.sidebar_width(), width + 1)
                screen.choose("appearance")
                screen.cancel(screen.page)
                self.assertEqual(view.theme.sidebar_width, width)
                self.assertFalse(screen.page.dirty)
                screen.choose(view.project_id)
                await until(lambda: getattr(screen.page, "identifier", None) == view.project_id and not screen.busy)
                page = screen.page
                self.assertIn(page.activate_button, page.buttons)
                self.assertNotIn(page.activate_button, page.body_widgets())
                self.assertEqual([button.width for button in page.buttons], [10, 10, 10, 10])
                count = page.component_forms["demo"].fields[("config", "parameters", "components", "demo", "config", "count")]
                count.input.text = "7"
                self.assertTrue(page.dirty)
                self.assertIn("demo", page.record["values"]["components"])
                screen.cancel(page)
                self.assertIn("demo", screen.page.components)
                self.assertFalse(screen.page.dirty)
                self.assertFalse(hasattr(screen.page, "toggles"))
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
