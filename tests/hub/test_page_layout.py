"""Shared page focus, fixed status geometry, and save-before-close behavior."""

import asyncio
from dataclasses import asdict
from functools import partial
import json
from pathlib import Path
import tempfile
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.config.preferences import PreferencesStore
from hub.config.theme import HubTheme
from hub.backend.runtime import HubConfig
from hub.backend.worker import BackendWorker
from hub.ui.application import create_application
from hub.ui.layout import TwoPanelPage
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput
from tests.hub.test_settings import settings_backend


class PageLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_focus_status_and_save_return(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            output = SizedOutput()
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                input=pipe, output=output,
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))

            async def paint():
                rendered.clear()
                app.invalidate()
                await asyncio.wait_for(rendered.wait(), 3)

            try:
                await until(lambda: view.connected)
                view.composer.text = "preserved draft"
                self.assertIsInstance(view.chat_page, TwoPanelPage)
                self.assertIsInstance(screen.layout, TwoPanelPage)
                for settings in (False, True):
                    if settings:
                        pipe.send_text("\x13")
                        await until(lambda: screen.page is not None and not screen.busy)
                    for key in ("\t", " ", "\r"):
                        pipe.send_text("\x1b")
                        await until(lambda: view.active_page.sidebar_focused)
                        await paint()
                        pipe.send_text("\x1b")
                        await until(lambda: not view.visible)
                        pipe.send_text("\x11")
                        await until(lambda: view.visible)
                        if not settings:
                            pipe.send_text("\x1b")
                            await until(lambda: view.active_page.sidebar_focused)
                        pipe.send_text(key)
                        await until(lambda: not view.active_page.sidebar_focused)
                    self.assertEqual(view.composer.text, "preserved draft")

                for columns, rows in ((132, 40), (80, 24)):
                    output.columns, output.rows = columns, rows
                    app._on_resize()
                    for notice in ("", view.t("settings_saved"), "long notice " * 100):
                        screen.status = notice
                        await paint()
                        positions = app.renderer._last_screen.visible_windows_to_write_positions
                        position = positions[screen.layout.status]
                        self.assertEqual((position.ypos, position.height), (rows - 2, 1))
                        self.assertEqual(position.xpos, view.sidebar_width() + 2)
                        self.assertEqual(position.width, columns - view.sidebar_width() - 3)
                        with set_app(app):
                            identifier = view.progress.start("Saving")
                        await paint()
                        progress_position = app.renderer._last_screen.visible_windows_to_write_positions[screen.layout.status]
                        self.assertEqual((progress_position.ypos, progress_position.height), (rows - 2, 1))
                        content = screen.layout.status.content.create_content(80, 1)
                        self.assertIn("Saving", "".join(part[1] for part in content.get_line(0)))
                        with set_app(app):
                            view.progress.finish(identifier)

                with set_app(app):
                    screen.choose("appearance")
                    screen.layout.focus_main()
                self.assertEqual(set(screen.page.form.fields), {(key,) for key in
                    ("background", "foreground", "accent1", "accent2", "accent3", "comment", "sidebar_width", "icon_style")})
                screen.page.form.fields[("accent1",)].input.text = "invalid"
                pipe.send_text("\x13")
                await until(lambda: "accent1" in screen.status)
                self.assertTrue(view.settings_open)
                screen.page.form.fields[("accent1",)].input.text = "#abcdef"
                pipe.send_text("\x13")
                await until(lambda: not view.settings_open)
                self.assertEqual(view.theme.accent1, "#abcdef")
                self.assertEqual(PreferencesStore(directory).load()["appearance"]["accent1"], "#abcdef")
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(view.composer.text, "preserved draft")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    def test_legacy_preferences_reduce_to_six_palette_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".hub" / "preferences.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"version": 1, "appearance": {
                "accent": "#123456", "user": "#234567", "muted": "#345678", "notice": "#456789",
                "header_background": "#111111", "button_focused_background": "#222222", "sidebar_width": 30}}))
            store = PreferencesStore(directory)
            values = store.load()["appearance"]
            self.assertEqual(values["accent1"], "#123456")
            self.assertEqual(values["comment"], "#345678")
            self.assertEqual(set(values), set(asdict(HubTheme())))
            store.save("appearance", values)
            self.assertEqual(store.load()["appearance"], values)
