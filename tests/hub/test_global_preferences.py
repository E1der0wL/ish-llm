from tests.hub.support import minimize
"""Global preferences, public usage queries and settings-to-shell focus."""

import asyncio
from functools import partial
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore, configured_profile
from hub.config.profile import UserProfile
from hub.backend.runtime import HubConfig
from hub.backend.project.settings import SettingsService
from hub.backend.worker import BackendWorker
from tests.hub.test_live import until
from tests.hub.support import SizedOutput, settings_button
from tests.hub.test_settings import settings_backend


class GlobalPreferenceTests(unittest.TestCase):
    def test_removed_mouse_option_is_rejected_without_rewriting_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(directory)
            for mode in ("auto", "hub", "terminal"):
                with self.subTest(mode=mode):
                    store._write_json({"version": 1, "general": {"mouse_mode": mode, "editor": "nano"}})
                    original = store.path.read_bytes()
                    with self.assertRaises(TypeError):
                        store.load()
                    with self.assertRaises(TypeError):
                        store.save("general", {"editor": "nano"})
                    self.assertEqual(store.path.read_bytes(), original)
            store._write_json({"version": 1, "general": {"unknown_option": True}})
            with self.assertRaises(TypeError):
                store.load()

    def test_created_profile_editor_roundtrip_and_language_on_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(directory)
            store.save("profile", {"display_name": "New profile"})
            self.assertEqual(store.load()["profile"]["email"], "")
            profile = {"display_name": "Reader", "language": "en", "email": "reader@example.com", "phone": "+82 10 1234 5678"}
            store.save("profile", profile)
            store.save("general", {"editor": "code --wait"})
            config = configured_profile(HubConfig(directory, model="test/model"))
            self.assertEqual(config.language, "en")
            self.assertEqual(config.user_profile, UserProfile(**profile))
            command = GeneralSettings(**store.load()["general"]).editor_argv(Path(directory) / "a b.txt")
            self.assertEqual(command, ["code", "--wait", str(Path(directory) / "a b.txt")])
            with patch.dict("os.environ", {"VISUAL": "nano -w", "EDITOR": "vim"}):
                self.assertEqual(GeneralSettings().editor_argv("a")[:2], ["nano", "-w"])
            with self.assertRaises(ValueError):
                store.save("profile", {"language": "unsupported"})
            with self.assertRaises(ValueError):
                store.save("general", {"editor": '"unterminated'})
            self.assertEqual(store.load()["profile"], profile)


class GlobalSettingsUITests(unittest.IsolatedAsyncioTestCase):
    async def test_public_usage_totals_periods_and_failure_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            totals = {"call_count": 4, "known_tokens": 120, "reserved_tokens": 30,
                      "unknown_calls": 1, "period_seconds": 3600, "calls": []}
            good = SimpleNamespace(id="good", aget_data=AsyncMock(return_value=SimpleNamespace(id="good", title="Good")),
                                   amodel_usage=AsyncMock(return_value=totals))
            bad = SimpleNamespace(id="bad", aget_data=AsyncMock(return_value=SimpleNamespace(id="bad", title="Bad")),
                                  amodel_usage=AsyncMock(side_effect=OSError("unreadable receipts")))
            runtime = SimpleNamespace(config=HubConfig(directory, model="test/model"), project=good,
                backend=SimpleNamespace(projects=SimpleNamespace(alist=AsyncMock(return_value=[good, bad])),
                                        describe_host=lambda: {"values": {}}))
            service = SettingsService(runtime)
            with patch.object(service, "_schema", return_value={}):
                catalog = await service.catalog()
            self.assertEqual(catalog["usage"][0]["known_tokens"], 120)
            self.assertEqual(catalog["usage"][0]["period_seconds"], 3600)
            self.assertNotIn("calls", catalog["usage"][0])
            self.assertEqual(catalog["usage"][1]["error"].message, "unreadable receipts")
            self.assertEqual(catalog["usage"][1]["error"].operation, "model_usage")
            good.amodel_usage.assert_awaited_once_with()

    async def test_settings_shell_return_drafts_buttons_and_general_page(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, model="test/model", auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, settings = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async())
            try:
                await until(lambda: app.is_running)
                shell = app.layout.current_control
                pipe.send_text('\x11\x10\r')
                await until(lambda: settings.page is not None and not settings.busy)
                field = settings.page.form.fields[("email",)]
                field.input.text = "unsaved@example.com"
                app.layout.focus(settings.global_list)
                app.invalidate()
                await until(lambda: not app.renderer._last_screen.show_cursor)
                await minimize(pipe, view)
                await until(lambda: not view.visible)
                self.assertIs(app.layout.current_control, shell)
                settings.loaded_catalog({"schema": settings.schema, "projects": settings.projects,
                                         "preferences": settings.drafts.preferences, "usage": settings.usage})
                self.assertIs(app.layout.current_control, shell)
                pipe.send_text("\x11")
                await until(lambda: view.visible)
                self.assertEqual(field.input.text, "unsaved@example.com")
                self.assertTrue(settings.page.dirty)
                self.assertIn("email", settings.page.form.schema["properties"])
                self.assertIn("known_tokens", settings.usage[0])
                await until(lambda: not settings.busy)
                settings.choose("general")
                self.assertEqual(settings.selected, "general")
                settings.page.form.fields[("editor",)].input.text = "nano -w"
                settings.page.save.handler()
                await until(lambda: not settings.busy and not settings.page.dirty)
                self.assertEqual(view.general.editor, "nano -w")
                for button in settings.page.buttons:
                    self.assertFalse(button.control.show_cursor)
                settings_button(app, pipe, view)
                from tests.hub.support import open_home
                await open_home(app, view)
                await until(lambda: app.renderer._last_screen.show_cursor)
                self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
