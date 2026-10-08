"""Cross-page settings transactions and constrained popup rendering."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.utils import get_cwidth

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.preferences import PreferencesStore
from hub.ui.application import create_application
from hub.ui.chat.history_ui import HistoryUI
from hub.ui.output import ImageRenderer, OutputBlock, RenderContext
from hub.config.theme import HubTheme
from hub.locales import Language
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_settings import settings_backend


class SettingsUpdates(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_draft_and_partial_save_leave_other_drafts_editable(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11\x13")
                await eventually(lambda: view.settings.page is not None)
                screen = view.settings
                with set_app(app):
                    screen.choose("profile")
                    profile = screen.page
                    profile.form.fields[("display_name",)].input.text = "changed"
                    screen.choose("appearance")
                    appearance = screen.page
                    appearance.form.fields[("sidebar_width",)].input.text = "invalid"
                calls = []
                def request(operation, args, done):
                    calls.append(operation)
                    done({"results": [{"display_name": "changed"}], "error": "save conflict"})
                screen.request = request
                screen.status = ""
                with set_app(app):
                    screen.save_and_close()
                self.assertTrue(screen.status)
                self.assertFalse(calls)
                self.assertTrue(view.settings_open)
                appearance.form.fields[("sidebar_width",)].input.text = "36"
                with set_app(app):
                    screen.save_and_close()
                self.assertIn("save conflict", screen.status)
                self.assertTrue(view.settings_open)
                self.assertFalse(screen.pages["profile"].dirty)
                self.assertTrue(screen.pages["appearance"].dirty)
                self.assertIs(screen.page, appearance)
                self.assertFalse(screen.busy)
            finally:
                app.exit()
                await task

    async def test_save_and_cancel_all_pages_from_sidebar_and_main(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                async def drafts(suffix):
                    pipe.send_text("\x13")
                    await until(lambda: screen.page is not None and not screen.busy and view.settings_open)
                    with set_app(app):
                        screen.choose("profile")
                        screen.page.form.fields[("display_name",)].input.text = "User " + suffix
                        screen.choose("appearance")
                        screen.page.form.fields[("sidebar_width",)].input.text = "35" if suffix == "saved" else "40"
                        screen.choose("general")
                        screen.page.form.fields[("editor",)].input.text = "nano " + suffix
                        screen.choose(view.project_id)
                    await until(lambda: getattr(screen.page, "identifier", None) == view.project_id and not screen.busy)
                    screen.page.form.fields[("title",)].input.text = "Project " + suffix
                for sidebar in (False, True):
                    await drafts("saved")
                    with set_app(app):
                        screen.switch_panel() if sidebar else screen.layout.focus_main()
                    pipe.send_text("\x13")
                    await until(lambda: not view.settings_open)
                    saved = PreferencesStore(Path(directory)).load()
                    self.assertEqual(saved["profile"]["display_name"], "User saved")
                    self.assertEqual(saved["appearance"]["sidebar_width"], 35)
                    self.assertEqual(saved["general"]["editor"], "nano saved")
                    self.assertEqual(view.general.editor, "nano saved")
                    await drafts("cancelled")
                    with set_app(app):
                        screen.switch_panel() if sidebar else screen.layout.focus_main()
                    pipe.send_text("\x03")
                    await until(lambda: not view.settings_open)
                    self.assertFalse(screen.pages)
                    self.assertEqual(view.theme.sidebar_width, 35)
                    self.assertEqual(PreferencesStore(Path(directory)).load(), saved)
                    pipe.send_text("\x13")
                    await until(lambda: not screen.busy and getattr(screen.page, "identifier", None) == view.project_id)
                    self.assertEqual(screen.page.form.fields[("title",)].input.text, "Project saved")
                    pipe.send_text("\x03")
                    await until(lambda: not view.settings_open)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_batch_validates_before_writing_and_reports_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False), settings_backend)
            await runtime.start()
            try:
                service = runtime.settings_service
                record = await service.load_project(runtime.project.id)
                bad = deepcopy(record["values"])
                bad["title"] = ""
                requests = [("save_global", ("profile", {"display_name": "changed"})),
                            ("save_project", (runtime.project.id, bad, record["config_version"]))]
                with self.assertRaises(ValueError):
                    await service.save_all(requests)
                self.assertNotEqual(service.preferences.load().get("profile", {}).get("display_name"), "changed")
                requests[1] = ("save_project", (runtime.project.id, record["values"], record["config_version"]))
                with patch.object(service, "save_project", new=AsyncMock(side_effect=ValueError("conflict"))):
                    result = await service.save_all(requests)
                self.assertEqual(len(result["results"]), 1)
                self.assertEqual(result["error"], "conflict")
            finally:
                await runtime.close()


class HistoryBorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_wide_turns_keep_popup_border_inside_terminal(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, _ = create_preview(input=pipe, output=output)
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                view.project_id = "project"
                history = HistoryUI(SimpleNamespace(view=view))
                rows = [{"id": str(i), "text": "요청🙂界" * 40, "status": "failed", "time": "2026-10-08",
                         "engine": "loop", "elapsed": 20, "response": "응답🙂界" * 80, "error": "오류" * 80}
                        for i in range(20)]
                with set_app(app):
                    history.show("session", rows)
                for columns, height in ((132, 40), (80, 24), (48, 20)):
                    output.columns, output.rows = columns, height
                    app.invalidate()
                    await asyncio.sleep(.2)
                    screen = app.renderer._last_screen
                    choices = view._dialog.body.children[1]
                    with set_app(app):
                        summary = fragment_list_to_text(choices.content.create_content(columns, 8).get_line(0))
                    self.assertLessEqual(get_cwidth(summary), min(80, columns - 4) - 7)
                    self.assertTrue(summary.endswith("…"))
                    corners = [(y, x) for y, row in screen.data_buffer.items() for x, cell in row.items()
                               if cell.char == "┐" and "class:dialog" in cell.style]
                    self.assertTrue(corners, (columns, height))
                    top, right = min(corners)
                    bottom = next((y for y in range(top + 1, height) if screen.data_buffer[y][right].char == "┘"), None)
                    self.assertIsNotNone(bottom, (columns, height, right))
                    self.assertLess(right, columns)
                    self.assertTrue(all(screen.data_buffer[y][right].char == "│" for y in range(top + 1, bottom)),
                                    (columns, right, [(y, ''.join(screen.data_buffer[y][x].char for x in range(columns)))
                                                     for y in range(top, bottom + 1)]))
            finally:
                app.exit()
                await task


class ImagePopupTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_concurrent_widths_share_completed_image_conversions(self):
        renderer = ImageRenderer()
        block = OutputBlock("hub-image", "", 0, (("src", "image.png"),))
        def context(width):
            return RenderContext(width, HubTheme(), Language("en"), Path("/project"), lambda: None)
        try:
            with patch.object(renderer, "_convert", return_value=[[("", "pixels")]]) as convert:
                for width in (30, 70):
                    renderer.render(block, context(width))
                    await eventually(lambda: not renderer._pending)
                for width in (30, 70, 30, 70):
                    self.assertIn("pixels", str(renderer.render(block, context(width))))
                self.assertEqual(convert.call_count, 2)
        finally:
            renderer.close()
