from llm.core.schema import implementation_schema
"""Public-schema forms, project lifecycle, and real PTK settings input."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.locales import Language
from hub.config.preferences import PreferencesStore
from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.settings.form import SchemaForm
from hub.config.theme import HubTheme
from hub.backend.worker import BackendWorker
from llm.components.base import Component
from llm.components.tools import ToolComponent
from llm.llm import LargeLanguageModel
from llm.engines.loop import LoopEngine
from tests.hub.test_live import ControlledEngine, until
from tests.hub.test_mockup import SizedOutput


class DemoComponent(Component):
    name = directory = "demo"

    def configuration_schema(self):
        return implementation_schema(config={'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1, 'description': 'Number of results'}, 'enabled': {'type': 'boolean', 'description': 'Enable processing'}, 'label': {'type': ['string', 'null'], 'description': 'Optional label'}}, 'additionalProperties': True})


def settings_backend(config, gate=None):
    gate = gate or threading.Event()
    return LargeLanguageModel(config.workspace, components=[ToolComponent(), DemoComponent()],
                              engines={"loop": ControlledEngine(gate)})


class SettingsFormTests(unittest.TestCase):
    def test_missing_null_nested_and_unknown_values_survive(self):
        schema = {"type": "object", "properties": {
            "nested": {"type": "object", "properties": {
                "count": {"type": "integer"}, "label": {"type": ["string", "null"]},
                "flag": {"type": "boolean"}}}}}
        original = {"nested": {"unknown": {"key.with.dots": 3}, "flag": False}}
        form = SchemaForm(schema, original, Language("en"))
        self.assertEqual(form.values(), original)
        self.assertEqual(original, {"nested": {"unknown": {"key.with.dots": 3}, "flag": False}})
        form.fields[("nested", "count")].input.text = "4"
        form.fields[("nested", "label")].input.text = "null"
        self.assertEqual(form.values()["nested"], {"unknown": {"key.with.dots": 3}, "flag": False,
                                                 "count": 4, "label": None})
        form.fields[("nested", "count")].input.text = "bad"
        with self.assertRaisesRegex(ValueError, "nested.count"):
            form.values()


class SettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_schema_save_clone_delete_and_component_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            runtime = HubRuntime(config, settings_backend)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                service = runtime.settings_service
                catalog = await service.catalog()
                self.assertIn("demo", catalog["schema"]["properties"]["components"]["items"]["enum"])
                record = await service.load_project(runtime.project.id)
                values = deepcopy(record["values"])
                values["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"] = "bad"
                with self.assertRaises(Exception):
                    await service.save_project(runtime.project.id, values, record["config_version"])
                self.assertNotIn("temperature", (await runtime.project.aget_data()).config.parameters["engines"]["loop"]["config"]["completion"])
                values["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"] = 0.4
                values["config"]["parameters"].setdefault("components", {})["demo"] = {"config": {"count": 3, "label": None}}
                saved = await service.save_project(runtime.project.id, values, record["config_version"])
                with self.assertRaises(ValueError):
                    await service.save_project(runtime.project.id, values, record["config_version"])
                names = await service.select_components(runtime.project.id, ["tools"], values["components"])
                self.assertEqual(names, ["tools"])
                self.assertEqual((await runtime.project.aget_data()).config.parameters.setdefault("components", {})["demo"]["config"]["count"], 3)
                await service.select_components(runtime.project.id, values["components"], ["tools"])
                created = await service.create_project({"title": "Created", "conversation_storage": "memory",
                    "components": ["demo"], "config": {"parameters": {"engines": {"loop": {'config': {'completion': {'model': 'test/other'}}}}, "components": {"demo": {'config': {'count': 2}}}}}})
                identifier = created["project"]["id"]
                clone = await service.clone_project(identifier, "Clone")
                self.assertEqual(clone["values"]["config"], created["values"]["config"])
                clone_id = clone["project"]["id"]
                await service.delete_project(clone_id)
                deleted = next(project for project in await runtime.backend.projects.alist(include_deleted=True)
                               if project.id == clone_id)
                self.assertTrue((await deleted.aget_data()).deleted)
                await service.activate_project(identifier)
                await runtime.new_session()
                self.assertEqual(runtime.project.id, identifier)
                self.assertEqual((await runtime.snapshot()).model, "test/other")
                changed = deepcopy(created["values"])
                changed["conversation_storage"] = "file"
                with self.assertRaisesRegex(ValueError, "Sessions"):
                    await service.save_project(identifier, changed, created["config_version"])
                with self.assertRaises(ValueError):
                    await service.delete_project(identifier)
            finally:
                await runtime.close()

    async def test_busy_project_is_not_cloned_and_preferences_persist(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            runtime = HubRuntime(config, partial(settings_backend, gate=gate))
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                await runtime.submit(runtime.selected_id, "hold request")
                with self.assertRaises(ValueError):
                    await runtime.settings_service.clone_project(runtime.project.id, "Busy")
                await runtime.settings_service.save_global("profile", {"display_name": "Profile name"})
                await runtime.settings_service.save_global("appearance", asdict(HubTheme(accent1="#abcdef")))
                self.assertEqual(runtime.config.user_profile.display_name, "Profile name")
                with self.assertRaises(ValueError):
                    await runtime.settings_service.save_global("appearance", {"accent1": "invalid"})
                stored = PreferencesStore(directory).load()
                self.assertEqual(stored["appearance"]["accent1"], "#abcdef")
                self.assertEqual(stored["profile"]["display_name"], "Profile name")
            finally:
                gate.set()
                await runtime.close()


class SettingsUITests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_setting_reaches_provider_and_persists_without_host_override(self):
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            yield {"choices": [{"index": 0, "delta": {"reasoning_content": "Provider summary"}, "finish_reason": None}]}
            yield {"choices": [{"index": 0, "delta": {"content": "Done"}, "finish_reason": "stop"}]}
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            path = ("config", "parameters", "engines", "loop", "config", "completion", "reasoning_effort")
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("\x13")
                await until(lambda: screen.page is not None and not screen.busy)
                screen.choose(view.project_id)
                await until(lambda: getattr(screen.page, "identifier", None) == view.project_id and not screen.busy)
                page = screen.page
                field = page.engine_forms["loop"].fields[path]
                self.assertEqual(field.input.text, "")
                self.assertNotIn("reasoning_effort", page.values()["config"]["parameters"]["engines"]["loop"]["config"]["completion"])
                app.layout.focus(field.input)
                pipe.send_text("\rlow\r")
                await until(lambda: field.input.text == "low" and not field.editing)
                app.layout.focus(page.save)
                pipe.send_text("\r")
                await until(lambda: screen.page is not page and not screen.busy)
                self.assertEqual(screen.page.engine_forms["loop"].fields[path].input.text, "low")
                pipe.send_text("\x13")
                await until(lambda: not view.settings_open)
                pipe.send_text("Explain\r")
                await until(lambda: any(m.role == "assistant" and m.status == "completed"
                                       for m in view.transcript.control.messages))
                self.assertEqual(calls[0]["reasoning_effort"], "low")
                self.assertTrue(any(m.role == "reasoning" and m.text == "Provider summary"
                                    for m in view.transcript.control.messages))
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            reopened = HubRuntime(config)
            try:
                await reopened.start()
                record = await reopened.settings_service.load_project(reopened.project.id)
                self.assertEqual(record["project"]["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["reasoning_effort"], "low")
                form = SchemaForm(record["schema"], record["values"], Language("ko"))
                form.fields[path].input.text = ""
                await reopened.settings_service.save_project(reopened.project.id, form.values(), record["config_version"])
                await reopened.submit(reopened.selected_id, "Use model default")
                await reopened.sessions[reopened.selected_id].run.wait_idle()
                self.assertNotIn("reasoning_effort", calls[-1])
                self.assertNotIn("reasoning_effort", (await reopened.project.aget_data()).config.parameters["engines"]["loop"]["config"]["completion"])
            finally:
                await reopened.close()

    async def test_ctrl_s_connects_backend_on_first_open(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, "loop", "test/model", auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            task = asyncio.create_task(app.run_async())
            try:
                await until(lambda: app.is_running)
                pipe.send_text("\x13")
                await until(lambda: controller.view.settings.page is not None)
                self.assertTrue(controller.view.settings_open)
                self.assertTrue(controller.view.settings.projects)
                self.assertIsNotNone(controller.view.settings.schema)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_ctrl_s_forms_components_validation_and_return_to_chat(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, "loop", "test/model", auto_title=False)
            app, controller = create_application(config, input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                if view.no_sessions:
                    pipe.send_text("\x1bOS\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text("keep chat draft\x13")
                await until(lambda: view.settings_open and screen.page is not None and not screen.busy)
                profile = screen.page
                profile.form.fields[("display_name",)].input.text = "Configured User"
                app.layout.focus(profile.save)
                pipe.send_text("\r")
                await until(lambda: screen.status == view.t("settings_saved"))
                screen.choose("appearance")
                appearance = screen.page
                appearance.form.fields[("accent1",)].input.text = "#abcdef"
                app.layout.focus(appearance.save)
                pipe.send_text("\r")
                await until(lambda: view.theme.accent == "#abcdef")
                identifier = view.project_id
                screen.choose(identifier)
                await until(lambda: screen.page is not appearance and not screen.busy)
                page = screen.page
                self.assertIn(("config", "parameters", "engines", "loop", "config", "completion", "model"), page.engine_forms["loop"].fields)
                demo_path = ("config", "parameters", "components", "demo", "config", "count")
                self.assertIn(demo_path, page.component_forms["demo"].fields)
                page.engine_forms["loop"].fields[("config", "parameters", "engines", "loop", "config", "completion", "temperature")].input.text = "wrong"
                page.submit()
                self.assertIn("temperature", screen.status)
                page.engine_forms["loop"].fields[("config", "parameters", "engines", "loop", "config", "completion", "temperature")].input.text = "0.7"
                page.component_forms["demo"].fields[demo_path].input.text = "5"
                self.assertIn("demo", page.components)
                self.assertFalse(hasattr(page, "toggles"))
                page.submit()
                await until(lambda: screen.page is not page and not screen.busy)
                self.assertEqual(screen.page.record["values"]["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"], 0.7)
                self.assertEqual(screen.page.record["values"]["config"]["parameters"]["components"]["demo"]["config"]["count"], 5)
                pipe.send_text("\t\x1b[Z\x13")
                await until(lambda: not view.settings_open)
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(view.composer.text, "keep chat draft")
                self.assertFalse(view.transcript.control.messages)
                pipe.send_text("\x13")
                await until(lambda: view.settings_open and not screen.busy)
                screen.choose("new")
                self.assertTrue(all(field.input.text == "" for field in screen.page.all_fields()))
                self.assertEqual(screen.page.values()["config"], {})
                screen.page.submit()
                await until(lambda: not screen.busy)
                self.assertIn(view.t("settings_title_required"), screen.status)
                screen.page.form.fields[("title",)].input.text = "UI project"
                screen.page.submit()
                await until(lambda: not screen.busy and not screen.page.new)
                self.assertEqual(screen.page.record["values"]["config"]["parameters"], {})
                self.assertEqual(screen.page.record["values"]["conversation_storage"], "file")
                created_id = screen.page.identifier
                screen.clone(screen.page)
                app.current_buffer.text = "UI clone"
                pipe.send_text("\t\r")
                try:
                    await until(lambda: not screen.busy and screen.page.identifier != created_id)
                except TimeoutError:
                    self.fail(f"Clone did not complete: {screen.status}; dialog={view._dialog is not None}; "
                              f"focus={app.layout.current_control!r}; buffer={app.current_buffer.text!r}")
                clone_id = screen.page.identifier
                self.assertEqual(screen.page.record["values"]["title"], "UI clone")
                screen.delete(screen.page)
                pipe.send_text("\r")
                await until(lambda: not screen.busy and screen.selected == "profile")
                self.assertNotIn(clone_id, [item["id"] for item in screen.projects])
                screen.choose(created_id)
                await until(lambda: not screen.busy and screen.selected == created_id)
                screen.activate(screen.page)
                await until(lambda: not view.settings_open and view.project_id == created_id)
                self.assertEqual(view.project_title, "UI project")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            _, reopened = create_application(config, input=pipe, output=SizedOutput())
            self.assertEqual(reopened.config.user_profile.display_name, "Configured User")
            self.assertEqual(reopened.view.theme.accent, "#abcdef")
            reopened.close()
