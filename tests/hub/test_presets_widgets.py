"""Preset transactions and reusable input widgets through public interfaces."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.presets import PresetStore
from hub.config.preferences import PreferencesStore
from hub.config.general import GeneralSettings
from hub.config.profile import UserProfile
from hub.config.theme import HubTheme
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.widget.controls import TextArea
from hub.widget.input_field import InputField
from hub.widget.tab_page import TabPage
from hub.ui.settings.pages import ProjectPage
from tests.hub.support import SizedOutput, eventually


class PresetBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_does_not_wait_for_ui_observation_lock(self):
        from hub.backend.worker import BackendWorker
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed = worker._restarting = False
        worker._failure = worker._snapshot_task = None
        worker._commands = set()
        worker.runtime = SimpleNamespace(lock=asyncio.Lock(), dirty=asyncio.Event(), interrupt=AsyncMock(return_value=True))
        async with worker.runtime.lock:
            self.assertTrue(await asyncio.wait_for(worker._execute("interrupt", ("s",)), .5))
        worker.runtime.interrupt.assert_awaited_once_with("s")

    async def test_export_save_apply_and_startup_template_preserve_identity(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                settings = runtime.settings_service
                pid = runtime.project.id
                value = await settings.execute("preset_export", pid)
                self.assertNotIn("title", value["project"])
                self.assertNotIn("_hub_title", value["project"]["config"]["parameters"]["engines"])
                value["global"]["profile"]["display_name"] = "Preset User"
                value["global"]["appearance"]["sidebar_width"] = 32
                value["project"]["agents"]["assistant"]["completion"] = {"model": "test/preset"}
                await settings.execute("preset_save", "Work", value)
                with self.assertRaises(ValueError):
                    await settings.execute("preset_save", "Work", value)
                result = await settings.execute("preset_apply", "Work", pid)
                self.assertIsNone(result["batch"]["error"])
                self.assertEqual(result["project"]["project"]["id"], pid)
                self.assertEqual(result["project"]["values"]["title"], "Hub")
                self.assertEqual(PreferencesStore(root).load()["profile"]["display_name"], "Preset User")
                self.assertEqual(PresetStore(root).read()["selected"], "Work")
                self.assertEqual(PresetStore(root).selected_project()["agents"]["assistant"]["completion"]["model"], "test/preset")
                self.assertEqual(len(await runtime.project.sessions.alist()), 0)
                # A malformed project must fail before any global preference write.
                invalid = deepcopy(value)
                invalid["global"]["profile"]["display_name"] = "Must not apply"
                invalid["project"]["config"]["policies"]["run"] = {"unknown_setting": True}
                with self.assertRaises(Exception):
                    await settings.execute("preset_save", "Invalid", invalid)
                self.assertEqual(PreferencesStore(root).load()["profile"]["display_name"], "Preset User")
            finally:
                await runtime.close()
            fresh = HubRuntime(HubConfig(Path(root) / "fresh", _workspace_anchor=Path(root), auto_title=False))
            await fresh.start()
            try:
                config = (await fresh.project.aget_data()).config
                self.assertEqual((await fresh.project.agents.aload("assistant"))["completion"]["model"], "test/preset")
            finally:
                await fresh.close()

    def test_preset_rejects_materializing_host_fixed_values(self):
        from hub.backend.project.presets import PresetService
        record = {"values": {"config": {"parameters": {"engines": {"loop": {"config": {"fixed": {"flag": True}}}}}}},
                  "effective_engines": {"loop-alias": {"editable": {"/config/fixed": False}}},
                  "schema": {"x-engines": {"loop-alias": {"configuration_key": "loop"}}}}
        candidate = deepcopy(record["values"])
        candidate["config"]["parameters"]["engines"]["loop"]["config"]["fixed"]["flag"] = 1
        with self.assertRaisesRegex(ValueError, "Host-fixed"):
            PresetService._check_fixed(record, candidate)
        record = {"values": {"config": {"parameters": {"components": {"rag": {"config": {}}}}}},
                  "components": {"rag": {"effective": {"editable": {"/config/fixed": False}}}}}
        candidate = deepcopy(record["values"])
        candidate["config"]["parameters"]["components"]["rag"]["config"]["fixed"] = "host"
        with self.assertRaisesRegex(ValueError, "Host-fixed"):
            PresetService._check_fixed(record, candidate)

    async def test_project_group_filter_preserves_paths_and_has_no_orphan_headers(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            view = LiveHubView()
            await runtime.start()
            try:
                record = await runtime.settings_service.load_project(runtime.project.id)
                page = ProjectPage(view.settings, record, suggest_defaults=False)
                page.set_section("engine:loop")
                before = page.values()
                self.assertEqual(len(page.groups), 1)
                for path, field in page.visible_forms()[0].fields.items():
                    self.assertFalse(field.label.startswith("config.parameters.engines.loop"))
                    self.assertTrue(field.schema.get("description"), path)
                page.filter("buffer_size")
                self.assertEqual(len(page.fields()), 1)
                self.assertEqual(len(page.container.children), 2)  # one heading + one field
                page.filter("no-such-option-ever")
                self.assertEqual(page.fields(), [])
                self.assertEqual(len(page.container.children), 1)  # empty-state only
                self.assertEqual(page.values(), before)
            finally:
                view.close()
                view.progress.close()
                await runtime.close()


class WidgetFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.composer.window.render_info)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.view.progress.close()

    async def test_export_current_settings_only_requires_name_not_editor(self):
        from hub.ui.settings.presets import EXPORT_CURRENT
        value = {"version": 1, "global": {"profile": asdict(UserProfile()), "appearance": asdict(HubTheme()),
                 "general": asdict(GeneralSettings())},
                 "project": {"config": {}, "components": [], "conversation_storage": "file"}}
        value["global"]["profile"]["display_name"] = "Current user"
        self.view.project_id = "p"
        calls = []
        def request(operation, args, done):
            calls.append((operation, args))
            done(value if operation == "preset_export" else {"names": ["Office"], "selected": ""})
        self.view.settings.request = request
        with set_app(self.app), patch("hub.ui.host.edit_text", new=AsyncMock()) as editor:
            ui = self.view.profile_bar.preset_ui
            self.view.profile_bar.presets.value = EXPORT_CURRENT
            ui.choose(EXPORT_CURRENT)
            self.assertEqual(self.view.profile_bar.presets.value, ui.selected)
            self.assertEqual(calls[0], ("preset_export", ("p",)))
            popup = self.view._dialog
            popup.body.children[2].text = "Office"
            self.pipe.send_text("\t\t\r")
            await eventually(lambda: self.view._dialog is None)
            self.assertEqual(calls[-1], ("preset_save", ("Office", value)))
            editor.assert_not_called()
            self.assertIsNone(self.view._dialog)

    async def test_dropdown_cancel_commit_and_profile_keyboard(self):
        dropdown = self.view.profile_bar.presets
        picked = []
        dropdown.items = [("a", "First"), ("b", "Second")]
        dropdown.value, dropdown.on_change = "a", picked.append
        self.pipe.send_text("\x10\x1b[C\r")
        await eventually(lambda: dropdown.expanded)
        await eventually(lambda: dropdown.choices.window in self.app.renderer._last_screen.visible_windows_to_write_positions)
        position = self.app.renderer._last_screen.visible_windows_to_write_positions[dropdown.choices.window]
        self.assertGreaterEqual(position.height, 2)
        self.pipe.send_text("\x1b[B\x1b")
        await eventually(lambda: not dropdown.expanded)
        self.assertEqual(picked, [])
        self.assertEqual(dropdown.value, "a")
        self.pipe.send_text(" \x1b[B\r")
        await eventually(lambda: picked == ["b"])
        self.assertIs(self.app.layout.current_control, dropdown.control)

    async def test_preset_creation_uses_configured_editor_and_validates_saved_json(self):
        value = {"version": 1, "global": {"profile": asdict(UserProfile()), "appearance": asdict(HubTheme()),
                "general": asdict(GeneralSettings())}, "project": {"config": {}, "components": [], "conversation_storage": "file"}}
        value["global"]["profile"]["display_name"] = "From editor"
        calls = []
        def request(operation, args, done):
            calls.append((operation, args))
            done({"names": [args[0]], "selected": ""})
        self.view.settings.request = request
        with set_app(self.app):
            self.view.profile_bar.preset_ui.create(value)
            popup = self.view._dialog
            name = popup.body.children[2]
            name.text = "Work"
        editor = AsyncMock(return_value=json.dumps(value))
        with patch("hub.ui.host.edit_text", editor):
            with set_app(self.app):
                name.button.handler()
            await eventually(lambda: editor.await_count == 1)
        self.pipe.send_text("\t\t\r")  # Name -> editor button -> Save.
        await eventually(lambda: self.view._dialog is None)
        self.assertEqual(calls[0][0], "preset_save")
        self.assertEqual(calls[0][1], ("Work", value))
        self.assertEqual(editor.await_args.args[1], self.view.general)

    async def test_input_field_action_and_tab_page_switch(self):
        actions = []
        field = InputField(text="draft", button_text="Use", on_action=lambda: actions.append(field.text))
        tabs = TabPage([("one", "One", field), ("two", "Two", TextArea(text="second"))])
        with set_app(self.app):
            self.view.open_dialog("Tabs", tabs, focus=field.input)
        self.pipe.send_text("\x01\x1b[200~replacement\x1b[201~\t ")
        await eventually(lambda: actions == ["replacement"])
        with set_app(self.app):
            tabs.select("two")
        self.assertEqual(tabs.page().text, "second")
        self.assertIs(self.app.layout.current_control, tabs.buttons[1].control)

    async def test_home_buttons_use_same_handlers_as_enter_and_ctrl_w(self):
        submitted, stopped = [], []
        self.view.on_submit = lambda sid, text: submitted.append(text)
        self.view.on_interrupt = stopped.append
        with set_app(self.app):
            self.view.composer.text = "request"
            self.view.composer_actions.send.handler()
            self.view.composer_actions.stop.handler()
        self.assertEqual(submitted, ["request"])
        self.assertEqual(stopped, [self.view.sessions[self.view.selected].id])

    async def test_drag_selection_is_replaced_by_terminal_paste_in_input_field(self):
        field = InputField(text="alpha beta", button_text="Use")
        with set_app(self.app):
            self.view.open_dialog("Input", field, focus=field.input)
        await eventually(lambda: field.input.window.render_info is not None)
        rect = self.app.renderer._last_screen.visible_windows_to_write_positions[field.input.window]
        x, y = rect.xpos, rect.ypos
        self.pipe.send_text(f'\x1b[<0;{x+1};{y+1}M\x1b[<32;{x+6};{y+1}M\x1b[<0;{x+6};{y+1}m')
        await eventually(lambda: field.buffer.selection_state is not None)
        self.pipe.send_text('\x1b[200~new\x1b[201~')
        await eventually(lambda: field.text == "new beta")


class PreparingDraftTests(unittest.TestCase):
    def test_user_is_visible_before_receipt_and_replaced_without_duplicates(self):
        view = LiveHubView()
        snapshot = HubSnapshot("p", "P", "m", "file", (SessionSummary("s", "S", "idle"),), "s", ())
        try:
            view.apply_snapshot(snapshot)
            view.preparing.start(("p", "s"), "Immediate request")
            user, assistant = view.output.transcript.control.messages
            self.assertEqual((user.role, user.status, user.text), ("user", "preparing", "Immediate request"))
            self.assertEqual(assistant.status, "preparing")
            view.preparing.accepted(("p", "s"), "saved")
            messages = view.preparing.project(("p", "s"), (ChatMessage("user", user.text, id="saved", status="queued"),))
            self.assertEqual(len([item for item in messages if item.role == "user"]), 1)
        finally:
            view.close()
            view.progress.close()
