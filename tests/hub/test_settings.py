
from llm.providers.litellm import LiteLLMCompletionAdapter
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
from tests.hub.support import open_home
from unittest.mock import AsyncMock, patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.application.current import set_app

from hub.ui.application import create_application
from hub.locales import Language
from hub.config.preferences import PreferencesStore
from hub.backend.runtime import HubConfig, HubRuntime
from hub.widget.forms.settings import SchemaForm
from hub.config.theme import HubTheme
from hub.backend.worker import BackendWorker
from llm.components.base import Component
from llm.components.tools import ToolComponent
from llm.llm import LargeLanguageModel
from llm.engines.loop import LoopEngine
from tests.hub.test_live import ControlledEngine, until
from tests.hub.support import settings_button, SizedOutput, backend_ready


class DemoComponent(Component):
    name = directory = "demo"

    def describe_config(self):
        return implementation_schema(config={'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1, 'description': 'Number of results'}, 'enabled': {'type': 'boolean', 'description': 'Enable processing'}, 'label': {'type': ['string', 'null'], 'description': 'Optional label'}}, 'additionalProperties': True})


def settings_backend(config, gate=None):
    gate = gate or threading.Event()
    return LargeLanguageModel(config.workspace, components=[ToolComponent(), DemoComponent()],
                              engines={"loop": ControlledEngine(gate)})


class SettingsFormTests(unittest.TestCase):
    def test_effective_values_are_observation_only_and_host_leaves_are_readonly(self):
        engine = ControlledEngine(threading.Event())
        from llm.core.models import ProjectConfig
        config = ProjectConfig(parameters={"engines": {"loop": {"config": {
            "system_prompt": "stored prompt", "completion": {"extra_body": {"sibling": 2}}}}}})
        original = config.parameters["engines"]["loop"]
        view = engine.resolve_config(config, "loop")
        # Generic adapter observations may still mark infrastructure leaves read-only.
        view["values"]["config"]["completion"]["extra_body"]["fixed"] = 1
        for path in ("/config/system_prompt", "/config/completion/extra_body/fixed"):
            view["sources"][path] = "host"
            view["editable"][path] = False
        schema = engine.describe_config()
        schema["properties"]["config"]["properties"]["completion"]["properties"]["extra_body"] = {
            "type": "object", "properties": {"fixed": {"type": "integer"}, "sibling": {"type": "integer"}}}
        form = SchemaForm(schema, original, Language("en"), effective={"adapter": view})
        prompt = form.fields[("config", "system_prompt")]
        fixed = form.fields[("config", "completion", "extra_body", "fixed")]
        self.assertTrue(prompt.readonly)
        self.assertTrue(fixed.readonly)
        self.assertEqual(prompt.observations, (Language("en")("settings_host_locked"),))
        self.assertEqual(fixed.input.text, "")
        self.assertEqual(form.values(), original)
        sibling = form.fields[("config", "completion", "extra_body", "sibling")]
        self.assertFalse(sibling.readonly)
        sibling.input.text = "3"
        fixed.input.text = "99"  # Even programmatic draft mutations must not leak into persistence.
        prompt.input.text = "ignored"
        result = form.values()
        self.assertEqual(result["config"]["system_prompt"], "stored prompt")
        self.assertEqual(result["config"]["completion"]["extra_body"], {"sibling": 3})
        self.assertEqual(original["config"]["completion"]["extra_body"], {"sibling": 2})

    def test_partial_host_json_is_guarded_without_locking_unrelated_keys(self):
        from llm.core.models import ProjectConfig
        engine = ControlledEngine(threading.Event())
        config = ProjectConfig(parameters={"engines": {"loop": {"config": {
            "completion": {"response_format": {"type": "text", "other": 1}}}}}})
        original = config.parameters["engines"]["loop"]
        view = engine.resolve_config(config, "loop")
        view["sources"]["/config/completion/response_format/type"] = "host"
        view["editable"]["/config/completion/response_format/type"] = False
        form = SchemaForm(engine.describe_config(), original, Language("en"), effective={"adapter": view})
        field = form.fields[("config", "completion", "response_format")]
        self.assertFalse(field.readonly)
        self.assertIn("nested keys", " ".join(field.observations))
        field.input.text = '{"type":"text","other":2}'
        self.assertEqual(form.values()["config"]["completion"]["response_format"], {"type": "text", "other": 2})
        field.input.text = '{"type":"json_object","other":2}'
        unchanged = deepcopy(original)
        with self.assertRaisesRegex(ValueError, "Host override"):
            form.apply(unchanged)
        self.assertEqual(unchanged, original)
        # JSON 편집기로 부모 객체를 바꿔도 bool/int의 Python 동등성으로 우회할 수 없다.
        original = {"config": {"locked": 1, "sibling": 1}}
        schema = implementation_schema(config={"type": "object"})
        view = {"values": original, "sources": {"/config/locked": "host"},
                "editable": {"/config/locked": False}}
        form = SchemaForm(schema, original, Language("en"), effective={"test": view})
        form.fields[("config",)].input.text = '{"locked":true,"sibling":2}'
        with self.assertRaisesRegex(ValueError, "Host override"):
            form.values()

    def test_runtime_host_and_client_values_are_not_materialized(self):
        engine = ControlledEngine(threading.Event())
        view = engine.resolve_config({}, "loop")
        form = SchemaForm(engine.describe_config(), {}, Language("en"), effective={"loop": view})
        model = form.fields[("config", "completion", "model")]
        self.assertFalse(model.readonly)
        self.assertEqual(model.observations, ())
        self.assertEqual(form.values(), {})
        schema = implementation_schema(config={"type": "object", "properties": {"a/b~c": {"type": "number"}}})
        view = {"values": {"config": {"a/b~c": 3}}, "sources": {"/config/a~1b~0c": "client"},
                "editable": {"/config/a~1b~0c": True}}
        component = SchemaForm(schema, {}, Language("ko"), effective={"component": view})
        self.assertEqual(component.values(), {})
        self.assertFalse(component.fields[("config", "a/b~c")].readonly)
        self.assertEqual(component.fields[("config", "a/b~c")].observations, ())

    def test_shared_settings_key_uses_all_engine_locks_and_views(self):
        engines = {"a": LoopEngine(parameter_key="shared"), "b": LoopEngine(parameter_key="shared")}
        from llm.core.models import ProjectConfig
        project = ProjectConfig(parameters={"engines": {"shared": {"config": {"buffer_size": 16}}}})
        schema = {"allOf": [engine.describe_config() for engine in engines.values()]}
        original = project.parameters["engines"]["shared"]
        views = {name: engine.resolve_config(project, "shared") for name, engine in engines.items()}
        form = SchemaForm(schema, {"shared": original}, Language("en"), prefix=("shared",), effective=views)
        field = form.fields[("shared",)]
        self.assertEqual(field.observations, ())
        self.assertEqual(form.values(), {"shared": original})
        field.input.text = '{"config":{"buffer_size":8}}'
        self.assertEqual(form.values()["shared"]["config"]["buffer_size"], 8)
        self.assertNotIn(("shared", "policy", "tool_timeout"), form.locked)

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
    async def test_prepared_settings_are_validated_before_writes_with_one_schema(self):
        from hub.backend.project.memory_setup import MemorySetup
        from jsonschema import ValidationError

        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False), settings_backend)
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            service = runtime.settings_service
            record = await service.load_project(runtime.project.id)
            original = (await runtime.project.aget_data()).config.to_dict()
            prepare = MemorySetup.prepare

            def invalid_preparation(config, components):
                candidate, selected = prepare(config, components)
                candidate.parameters.setdefault('components', {})['demo'] = {'config': {'count': 0}}
                return candidate, selected

            with patch.object(MemorySetup, 'prepare', side_effect=invalid_preparation), \
                    patch.object(service, '_schema', wraps=service._schema) as schema_reads:
                with self.assertRaises(ValidationError):
                    await service.save_project(runtime.project.id, record['values'], record['config_version'])
            self.assertEqual(schema_reads.call_count, 1)
            self.assertEqual((await runtime.project.aget_data()).config.to_dict(), original)
            # A later operation has its own schema and can still save normally.
            with patch.object(service, '_schema', wraps=service._schema) as next_schema:
                await service.save_project(runtime.project.id, record['values'], record['config_version'])
            self.assertEqual(next_schema.call_count, 2)

    async def test_settings_keep_the_selected_adapters_declared_fields(self):
        from types import SimpleNamespace
        from hub.backend.project.settings import SettingsService
        from tests.llm.test_common_provider_acceptance import IndependentAdapter
        adapter = IndependentAdapter()
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, engines={
                    "custom": LoopEngine(completion_adapter=adapter)}) as backend:
                service = SettingsService(SimpleNamespace(backend=backend,
                    config=HubConfig(directory, auto_title=False), t=Language()))
                schema = service._schema()
                engine = schema["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]["custom"]
                project = await backend.projects.acreate("Schema", components=[], conversation_storage="file")
                request = (await project.agents.adescribe(engine="custom"))["schema"]["properties"]["completion"]
                self.assertEqual(set(request["properties"]), {"model", "output_cap", "wait"})
                self.assertNotIn("reasoning_effort", request["properties"])
                self.assertFalse(request["additionalProperties"])
                self.assertEqual(adapter.requests, [])

    async def test_project_schema_save_clone_delete_and_component_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, model="test/model", auto_title=False)
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
                values["config"]["parameters"].setdefault("engines", {}).setdefault("loop", {}).setdefault("config", {}).setdefault("completion", {})["temperature"] = "bad"
                with self.assertRaises(Exception):
                    await service.save_project(runtime.project.id, values, record["config_version"])
                self.assertNotIn("temperature", (await runtime.project.aget_data()).config.parameter_targets.engine("loop").config.get("completion", {}))
                values["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"] = 0.4
                values["config"]["parameters"].setdefault("components", {})["demo"] = {"config": {"count": 3, "label": None}}
                with patch.object(service, '_schema', wraps=service._schema) as schema_reads:
                    saved = await service.save_project(runtime.project.id, values, record["config_version"])
                # Original/prepared drafts share one schema before any await;
                # the returned editor reads a fresh schema after persistence.
                self.assertEqual(schema_reads.call_count, 2)
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
                project = await runtime.backend.projects.aload(identifier)
                agent = await project.agents.asnapshot('assistant')
                agent['definition']['completion'] = {'model':'test/other'}
                await project.agents.arevise('assistant', agent['definition'], expected_revision=agent['revision'])
                await service.activate_project(identifier)
                await runtime.new_session()
                self.assertEqual(runtime.project.id, identifier)
                self.assertEqual((await runtime.snapshot()).model, "test/other")
                changed = deepcopy(created["values"])
                changed["conversation_storage"] = "file"
                with self.assertRaisesRegex(ValueError, "Sessions"):
                    await service.save_project(identifier, changed, created["config_version"])
                await service.delete_project(identifier)
                self.assertNotEqual(runtime.project.id, identifier)
                deleted = next(project for project in await runtime.backend.projects.alist(include_deleted=True)
                               if project.id == identifier)
                self.assertTrue((await deleted.aget_data()).deleted)
                with self.assertRaisesRegex(ValueError, runtime.t("project_delete_last")):
                    await service.delete_project(runtime.project.id)
            finally:
                await runtime.close()

    async def test_busy_project_is_not_cloned_and_preferences_persist(self):
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, model="test/model", auto_title=False)
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
    async def test_project_form_execution_config_and_agent_instructions_are_separate(self):
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            yield {"choices": [{"index": 0, "delta": {"content": "Done"}, "finish_reason": "stop"}]}
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            config = HubConfig(directory, model="test/model", auto_title=False,
                agent_definition={"engine": "loop", "system_prompt": "agent instructions"},
                project_config={"parameters": {"engines": {"loop": {"config": {"buffer_size": 16}}}}},
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await backend_ready(lambda: view.connected and not view.projects.busy, 'initial connection')
                if view.no_sessions:
                    pipe.send_text("/new\r\r")
                    await backend_ready(lambda: not view.no_sessions and view._dialog is None, 'session creation')
                pipe.send_text('\x10\r')
                await backend_ready(lambda: screen.page is not None and not screen.busy, 'settings catalog')
                screen.choose("engine:loop")
                await backend_ready(lambda: getattr(screen.page, "identifier", None) == view.project_id and not screen.busy, 'project settings')
                page = screen.page
                prefix = ("config", "parameters", "engines", "loop", "config")
                self.assertNotIn((*prefix, "system_prompt"), page.engine_forms["loop"].fields)
                field = page.engine_forms["loop"].fields[(*prefix, "buffer_size")]
                self.assertFalse(field.readonly)
                self.assertEqual(field.input.text, "16")
                self.assertEqual(field.observations, ())
                field.input.text = "8"
                page.save.handler()
                await backend_ready(lambda: screen.page is not page and not screen.busy, 'settings saved')
                stored = screen.page.record["project"]["config"]["parameters"]["engines"]["loop"]["config"]
                self.assertEqual(stored["buffer_size"], 8)
                self.assertNotIn("system_prompt", stored)
                self.assertNotIn("completion", stored)
                settings_button(app, pipe, view, "save")
                await open_home(app, view)
                pipe.send_text("hello\r")
                await backend_ready(lambda: any(m.role == "assistant" and m.status == "completed" for m in view.output.transcript.control.messages), 'request completed')
                self.assertEqual(calls[0]["messages"][0], {"role": "system", "content": "agent instructions"})
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_reasoning_setting_reaches_provider_and_persists_without_host_override(self):
        calls = []
        def provider(**kwargs):
            calls.append(kwargs)
            yield {"choices":[{"index":0,"delta":{"content":"Done"},"finish_reason":"stop"}]}
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            config = HubConfig(root, model='test/model', auto_title=False,
                engine_factories={'loop':lambda:LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda:view.show(app)))
            try:
                await backend_ready(lambda:view.connected, 'initial connection')
                with set_app(app):
                    controller.component_commands.open('agent', '', consume_input=False, edit_identifier='assistant')
                from hub.widget.forms.agent_editor import AgentEditor
                await backend_ready(lambda:isinstance(view._dialog, AgentEditor) and not view._dialog.busy, 'agent editor')
                editor = view._dialog
                self.assertIn(('completion','reasoning_effort'), editor.form.fields, list(editor.form.fields))
                field = editor.form.fields[('completion','reasoning_effort')]
                self.assertEqual(field.input.text, '')
                with set_app(app):
                    field.input.text = 'low'
                    editor.save()
                await backend_ready(lambda:view._dialog is editor.manager, 'agent settings saved')
                with set_app(app):
                    view.close_dialog()
                pipe.send_text('/new\r\r')
                await backend_ready(lambda:not view.no_sessions and view._dialog is None, 'session creation')
                pipe.send_text('Explain\r')
                await backend_ready(lambda:any(m.role=='assistant' and m.status=='completed' for m in view.output.transcript.control.messages), 'request completed')
                self.assertEqual(calls[0]['reasoning_effort'],'low')
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            runtime=HubRuntime(config)
            try:
                await runtime.start()
                saved=await runtime.project.agents.asnapshot('assistant')
                self.assertEqual(saved['definition']['completion']['reasoning_effort'],'low')
                saved['definition']['completion'].pop('reasoning_effort')
                await runtime.project.agents.arevise('assistant',saved['definition'],expected_revision=saved['revision'])
                await runtime.submit(runtime.selected_id,'Use model default')
                await runtime.sessions[runtime.selected_id].run.wait_idle()
                self.assertNotIn('reasoning_effort',calls[-1])
            finally:
                await runtime.close()

    async def test_ctrl_q_then_ctrl_s_connects_backend_on_first_open(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, model="test/model", auto_title=False),
                input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            task = asyncio.create_task(app.run_async())
            try:
                await until(lambda: app.is_running)
                pipe.send_text('\x11\x10\r')
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
            config = HubConfig(directory, model="test/model", auto_title=False)
            app, controller = create_application(config, input=pipe, output=SizedOutput(),
                worker_factory=partial(BackendWorker, backend_factory=settings_backend))
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected and not view.projects.busy)
                if view.no_sessions:
                    pipe.send_text("/new\r\r")
                    await until(lambda: not view.no_sessions and view._dialog is None)
                pipe.send_text('keep chat draft\x10\r')
                await until(lambda: view.settings_open and screen.page is not None and not screen.busy)
                screen.choose("profile")
                profile = screen.page
                profile.form.fields[("display_name",)].input.text = "Configured User"
                app.layout.focus(profile.save)
                pipe.send_text("\r")
                await open_home(app, view)
                pipe.send_text('\x10\r')
                await until(lambda: view.settings_open and not screen.busy)
                screen.choose("appearance")
                appearance = screen.page
                appearance.form.fields[("accent1",)].input.text = "#abcdef"
                app.layout.focus(appearance.save)
                pipe.send_text("\r")
                await until(lambda: view.theme.accent == "#abcdef" and not screen.busy)
                await open_home(app, view)
                pipe.send_text('\x10\r')
                await until(lambda: view.settings_open and not screen.busy)
                identifier = view.project_id
                screen.choose(identifier)
                await until(lambda: screen.page is not appearance and not screen.busy)
                page = screen.page
                self.assertIn(("config", "parameters", "engines", "loop", "config", "completion", "model"), page.engine_forms["loop"].fields)
                demo_path = ("config", "parameters", "components", "demo", "config", "count")
                self.assertIn(demo_path, page.component_forms["demo"].fields)
                page.engine_forms["loop"].fields[("config", "parameters", "engines", "loop", "config", "completion", "temperature")].input.text = "wrong"
                page.save.handler()
                self.assertIn("temperature", screen.status)
                page.engine_forms["loop"].fields[("config", "parameters", "engines", "loop", "config", "completion", "temperature")].input.text = "0.7"
                page.component_forms["demo"].fields[demo_path].input.text = "5"
                self.assertIn("demo", page.components)
                self.assertFalse(hasattr(page, "toggles"))
                page.save.handler()
                await until(lambda: screen.page is not page and not screen.busy)
                self.assertEqual(screen.page.record["values"]["config"]["parameters"]["engines"]["loop"]["config"]["completion"]["temperature"], 0.7)
                self.assertEqual(screen.page.record["values"]["config"]["parameters"]["components"]["demo"]["config"]["count"], 5)
                settings_button(app, pipe, view, "save")
                await open_home(app, view)
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(view.composer.text, "keep chat draft")
                self.assertFalse(view.output.transcript.control.messages)
                await until(lambda: view.projects.loaded and not view.projects.busy)
                with set_app(app):
                    app.layout.focus(view.projects.control)
                    view.projects.perform("c")
                app.current_buffer.text = "UI project"
                pipe.send_text("\r")
                await until(lambda: view.project_title == "UI project" and not view.projects.busy)
                created_id = view.project_id
                with set_app(app):
                    view.projects.perform("r")
                app.current_buffer.text = "UI clone"
                pipe.send_text("\r")
                await until(lambda: view.project_title == "UI clone" and not view.projects.busy)
                clone_id = view.project_id
                with set_app(app):
                    view.projects.perform("d")
                pipe.send_text("\r")
                await until(lambda: not view.projects.busy and clone_id not in [item["id"] for item in view.projects.items])
                with set_app(app):
                    view.projects.select(created_id)
                await until(lambda: view.project_id == created_id and not view.projects.busy)
                self.assertEqual(view.project_title, "UI project")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)
            _, reopened = create_application(config, input=pipe, output=SizedOutput())
            self.assertEqual(reopened.config.user_profile.display_name, "Configured User")
            self.assertEqual(reopened.view.theme.accent, "#abcdef")
            reopened.close()
