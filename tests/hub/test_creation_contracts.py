"""Creation presets must become visible settings, never runtime authority."""

from llm.providers.litellm import LiteLLMCompletionAdapter

from copy import deepcopy
from dataclasses import asdict
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore
from hub.config.presets import PresetStore
from hub.config.profile import UserProfile
from hub.config.theme import HubTheme


class CreationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_opening_never_reenables_tools_or_executes_an_excluded_effect(self):
        from hub.backend.tools.builtin import HubBuiltinTools
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, call, chunk
        for components, options in (([], {}), (["builtin_tools"], {}),
                                    (["builtin_tools"], {"enabled": []})):
            with self.subTest(components=components, options=options), tempfile.TemporaryDirectory() as root:
                effect = Path(root) / "must-not-exist"
                provider = ScriptedCompletion([chunk(calls=[call(
                    '{"path":"must-not-exist","content":"unexpected"}', name="file_create")],
                    finish="tool_calls")])
                config = HubConfig(Path(root) / "workspace", auto_title=False, file_root=root,
                                   model="test/model",
                                   engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
                runtime = HubRuntime(config)
                await runtime.start()
                try:
                    project = runtime.project
                    identifier = project.id
                    record = await project.aget_data()
                    values = record.config.to_dict()
                    values["parameters"]["components"] = (
                        {"builtin_tools": {"config": options}} if components else {})
                    await project.asave(config=values, components=components)
                    before = await runtime.settings_service.load_project(identifier)
                finally:
                    await runtime.close()
                runtime = HubRuntime(config)
                # A changed creation preset is irrelevant when opening saved data.
                with patch.object(HubBuiltinTools, "select_tools", side_effect=AssertionError("preset reapplied")):
                    await runtime.start()
                try:
                    reopened = await runtime.settings_service.load_project(identifier)
                    self.assertEqual(reopened["values"], before["values"])
                    self.assertEqual(reopened["config_version"], before["config_version"])
                    sid = await runtime.new_session()
                    await runtime.submit(sid, "Try a disabled Tool")
                    await runtime.sessions[sid].run.wait_idle()
                    run = (await runtime.sessions[sid].run.alist())[-1]
                    self.assertEqual(str((await run.aresult()).status), "failed")
                    self.assertEqual(len(provider.requests), 0)
                    self.assertFalse(effect.exists())
                    self.assertFalse(await run.ainteractions())
                    self.assertEqual((await runtime.settings_service.load_project(identifier))["values"], before["values"])
                    # The saved Agent still selects now-unavailable Tools. The Application must
                    # explicitly revise its choice before testing an unadvertised Tool response.
                    selected = await runtime.project.agents.asnapshot(config.agent_id)
                    definition = selected["definition"]
                    definition["tools"] = []
                    await runtime.project.agents.arevise(config.agent_id, definition,
                                                         expected_revision=selected["revision"])
                    await runtime.submit(sid, "Try a disabled Tool")
                    await runtime.sessions[sid].run.wait_idle()
                    run = (await runtime.sessions[sid].run.alist())[-1]
                    self.assertEqual(str((await run.aresult()).status), "failed")
                    self.assertEqual(len(provider.requests), 1)
                    self.assertFalse(provider.requests[0].get("tools"))
                    self.assertFalse(effect.exists())
                    self.assertFalse(await run.ainteractions())
                    self.assertEqual(
                        (await runtime.settings_service.load_project(identifier))["values"], before["values"])
                finally:
                    await runtime.close()

    async def test_explicit_empty_components_and_falsey_policies_survive_reopen(self):
        config = {"policies": {
            "tool_retry": {"max_retries": 0, "delay_seconds": None},
            "approval": {"enabled": False, "rules": []},
            "tools": {"allowed_tools": [], "max_calls": None},
        }, "parameters": {"engines": {"loop": {"config": {"system_prompt": None}}}}}
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                service = runtime.settings_service
                created = await service.create_project({
                    "title": "Explicit", "config": config, "components": [],
                    "conversation_storage": "memory"})
                identifier = created["project"]["id"]
                self.assertEqual(created["values"]["components"], [])
                self.assertEqual(created["values"]["config"]["policies"], config["policies"])
                self.assertIsNone(created["values"]["config"]["parameters"]["engines"]["loop"]["config"]["system_prompt"])
                edited = deepcopy(created["values"])
                edited["config"]["policies"]["tool_retry"] = {"max_retries": None, "delay_seconds": 0}
                await service.save_project(identifier, edited, created["config_version"])
                # Even an explicit batch request must respect the same creation contract.
                batch = await service.save_all([("create_project", ({
                    "title": "Batch", "config": {}, "components": []},))])
                self.assertIsNone(batch["error"])
                self.assertEqual(batch["results"][0]["values"]["components"], [])
            finally:
                await runtime.close()
            runtime = HubRuntime(HubConfig(root, project_id=identifier, auto_title=False))
            await runtime.start()
            try:
                reopened = await runtime.settings_service.load_project(identifier)
                self.assertEqual(reopened["values"], edited)
            finally:
                await runtime.close()

    async def test_creation_preset_cannot_override_backend_limit_validation(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                before = {project.id for project in await runtime.backend.projects.alist()}
                for invalid in (0, False, -1):
                    with self.subTest(max_calls=invalid), self.assertRaises(Exception):
                        await runtime.settings_service.create_project({
                            "title": "Invalid", "components": [],
                            "config": {"policies": {"tools": {"max_calls": invalid}}}})
                self.assertEqual({project.id for project in await runtime.backend.projects.alist()}, before)
            finally:
                await runtime.close()


class PresetStorageContractTests(unittest.TestCase):
    def value(self):
        return {"version": 1, "global": {
            "profile": asdict(UserProfile(email="")),
            "appearance": asdict(HubTheme()),
            "general": asdict(GeneralSettings())},
            "project": {"config": {}, "components": [], "conversation_storage": "memory"}}

    def test_current_roundtrip_is_detached_and_preserves_explicit_empty_values(self):
        with tempfile.TemporaryDirectory() as root:
            store = PresetStore(root)
            value = self.value()
            store.save("Empty", value)
            store.select("Empty")
            self.assertEqual(store.read()["presets"]["Empty"], value)
            selected = store.selected_project()
            self.assertEqual(selected, value["project"])
            selected["components"].append("tools")
            self.assertEqual(store.selected_project()["components"], [])

    def test_incomplete_stored_global_section_is_rejected_without_rewriting(self):
        for section in ("profile", "appearance", "general"):
            with self.subTest(section=section), tempfile.TemporaryDirectory() as root:
                store = PresetStore(root)
                value = self.value()
                value["global"][section] = {}
                source = {"version": 1, "selected": "Broken", "presets": {"Broken": value}}
                store._write_json(source)
                before = store.path.read_bytes()
                with self.assertRaises((TypeError, ValueError)):
                    store.selected_project()
                self.assertEqual(store.path.read_bytes(), before)
                self.assertFalse(PreferencesStore(root).path.exists())

    def test_entire_store_is_validated_before_selection_or_write(self):
        mutations = (
            lambda value: value.update(extra=True),
            lambda value: value.update(version=True),
            lambda value: value.update(version=1.0),
            lambda value: value.update(selected="Missing"),
            lambda value: value["presets"]["Saved"]["project"].update(config={"unexpected": {}}),
            lambda value: value["presets"]["Saved"].update(version=False),
            lambda value: value["presets"]["Saved"]["global"]["general"].pop("language"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as root:
                store = PresetStore(root)
                record = {"version": 1, "selected": "", "presets": {"Saved": self.value()}}
                mutate(record)
                store._write_json(record)
                before = store.path.read_bytes()
                with self.assertRaises((TypeError, ValueError)):
                    store.read()
                with self.assertRaises((TypeError, ValueError)):
                    store.save("New", self.value())
                self.assertEqual(store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
