from llm.core.schema import implementation_schema
"""설정 저장·UI 미리보기·Engine 공유 키의 검증 계약을 회귀 검증한다."""

from copy import deepcopy
import tempfile
import unittest

from jsonschema import Draft202012Validator
from llm.components.base import Component
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.llm import LargeLanguageModel


class ConnectionComponent(Component):
    name = directory = "connection"

    def configuration_schema(self):
        return implementation_schema(config={'type': 'object', 'required': ['connection', 'peers'], '$defs': {'address': {'type': 'object', 'required': ['host', 'port'], 'properties': {'host': {'type': 'string'}, 'port': {'type': 'integer', 'minimum': 1}}}}, 'properties': {'connection': {'$ref': '#/properties/config/$defs/address'}, 'peers': {'type': 'array', 'items': {'$ref': '#/properties/config/$defs/address'}}}})


class SchemaEngine:
    """설정 해석기 없이 스키마만 공개하는 제3자 Engine."""
    def __init__(self, minimum, maximum):
        self.minimum, self.maximum = minimum, maximum

    def configuration_schema(self):
        return implementation_schema(config={'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': self.minimum, 'maximum': self.maximum}}}, **{'x-settings-key': 'shared'})

    async def execute(self, context):
        raise AssertionError("Validation must never execute Engines")
        yield


class ConfigurationValidationTests(unittest.IsolatedAsyncioTestCase):
    def backend(self, *, engines=None, components=()):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = LargeLanguageModel(directory.name, engines=engines or {"loop": LoopEngine()}, components=components)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def test_invalid_project_create_and_default_leave_no_metadata(self):
        app = self.backend()
        invalid = {"parameters": {"engines": {"loop": {'policy': {'max_iterations': 0}}}}}
        for operation in (app.projects.acreate,):
            with self.assertRaisesRegex(ValueError, "loop"):
                await operation(config=invalid)
        self.assertEqual(await app.projects.alist(), [])
        self.assertFalse((app.project_manager.repository.root / "default-project.json").exists())

    async def test_project_and_session_validate_before_persistence(self):
        app = self.backend(engines={"loop": LoopEngine()})
        invalid = {"parameters": {"engines": {"loop": {'policy': {'max_iterations': 0}}}}}
        with self.assertRaisesRegex(ValueError, "project"):
            await app.projects.acreate(config=invalid)
        project = await app.projects.acreate(config={"parameters": {"engines": {"loop": {'policy': {'max_iterations': 2}}}}})
        before = (project.paths.root / "project.json").read_bytes()
        for operation in (project.asave, project.avalidate_configuration, project.sessions.acreate):
            with self.assertRaises(ValueError):
                await operation(config=invalid)
        self.assertEqual((project.paths.root / "project.json").read_bytes(), before)
        view = await project.aconfiguration()
        Draft202012Validator(view["schema"]).validate(view["values"])
        self.assertEqual(view["effective_engines"]["loop"]["values"]["policy"]["max_iterations"], 2)

    async def test_invalid_save_preserves_project_and_session_records(self):
        app = self.backend()
        project = await app.projects.acreate()
        session = await project.sessions.acreate()
        for handle, file in ((project, "project.json"), (session, "session.json")):
            path = handle.paths.root / file
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "loop"):
                await handle.asave(config={"parameters": {"engines": {"loop": {'policy': {'max_iterations': 0}}}}})
            self.assertEqual(path.read_bytes(), before)

    async def test_session_defaults_and_explicit_overrides_validate_before_creation(self):
        app = self.backend()
        invalid = {"parameters": {"engines": {"loop": {'policy': {'request_timeout': -1}}}}}
        with self.assertRaisesRegex(ValueError, "session_defaults"):
            await app.projects.acreate(config={"session_defaults": invalid})
        project = await app.projects.acreate()
        with self.assertRaisesRegex(ValueError, "loop"):
            await project.sessions.acreate(config=invalid)
        self.assertEqual(await project.sessions.alist(), [])

    async def test_graph_settings_validate_without_loading_workflow(self):
        app = self.backend(engines={"graph": GraphEngine(handlers={})})
        with self.assertRaisesRegex(ValueError, "graph"):
            await app.projects.acreate(config={"parameters": {"engines": {"graph": {'policy': {'max_parallelism': 0}}}}})
        await app.projects.acreate(config={"parameters": {"engines": {"graph": {'policy': {'max_parallelism': 2}}}}})

    async def test_pipeline_rejects_invalid_stage_without_preparation(self):
        async def prepare(context):
            raise AssertionError("Configuration must not run preparation")
        app = self.backend(engines={"pipeline": PipelineEngine({
            "prepare": PreparationStep("prepare", prepare), "answer": LoopEngine()})})
        for stages in ({"prepare": {"timeout_seconds": -1}},
                       {"answer": {"max_iterations": 0}}, {"unknown": {}}):
            with self.assertRaisesRegex(ValueError, "pipeline"):
                await app.projects.acreate(config={"parameters": {"engines": {"pipeline": {'config': {'stages': stages}}}}})
        await app.projects.acreate(config={"parameters": {"engines": {"pipeline": {'config': {'stages': {'prepare': {'policy': {'timeout_seconds': None}}, 'answer': {'policy': {'max_iterations': 3}}}}}}}})

    async def test_partial_nested_defaults_match_ui_without_weakening_arrays(self):
        app = self.backend(components=[ConnectionComponent()])
        raw = {"parameters": {"components": {"connection": {'config': {'connection': {'host': 'localhost', 'port': 9000}, 'peers': []}}}}}
        project = await app.projects.acreate(config=raw, components=["connection"])
        view = await project.aconfiguration()
        Draft202012Validator(view["schema"]).validate(view["values"])
        self.assertEqual(view["values"]["config"]["parameters"]["components"]["connection"]["config"]["connection"],
                         {"host": "localhost", "port": 9000})
        self.assertEqual(view["project"]["config"]["parameters"]["components"], raw["parameters"]["components"])
        broken = deepcopy(view["values"])
        broken["config"]["parameters"]["components"]["connection"]["config"]["peers"] = [{"port": 90}]
        self.assertFalse(Draft202012Validator(view["schema"]).is_valid(broken))
        with self.assertRaises(ValueError):
            await project.avalidate_configuration(broken["config"])

    async def test_preview_is_detached_and_keeps_saved_versions(self):
        app = self.backend(components=[ConnectionComponent()])
        project = await app.projects.acreate(components=["connection"], config={"parameters": {"components": {"connection": {'config': {'connection': {'host': 'localhost', 'port': 8000}, 'peers': []}}}}})
        original = await project.aconfiguration()
        path = project.paths.root / "project.json"
        before = path.read_bytes()
        candidate = deepcopy(original["project"]["config"])
        candidate["parameters"]["components"]["connection"]["config"]["connection"]["port"] = 9001
        preview = await project.avalidate_configuration(candidate, expected_version=original["config_version"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(preview["config_version"], original["config_version"])
        self.assertEqual(preview["component_versions"], original["component_versions"])
        self.assertEqual(candidate["parameters"]["components"]["connection"]["config"]["connection"]["host"], "localhost")
        Draft202012Validator(preview["schema"]).validate(preview["values"])
        await project.asave(config=preview["project"]["config"], expected_version=preview["config_version"])
        with self.assertRaisesRegex(ValueError, "changed"):
            await project.asave(config=candidate, expected_version=preview["config_version"])
        with self.assertRaisesRegex(ValueError, "changed"):
            await project.avalidate_configuration(candidate, expected_version=original["config_version"])

    async def test_inactive_component_defaults_are_previewed_without_initialization(self):
        app = self.backend(components=[ConnectionComponent()])
        project = await app.projects.acreate(config={"parameters": {"components": {
            "connection": {'config': {'connection': {'host': 'localhost', 'port': 9010}, 'peers': []}}}}})
        view = await project.aconfiguration()
        Draft202012Validator(view["schema"]).validate(view["values"])
        self.assertFalse((project.paths.root / "connection").exists())
        self.assertEqual(view["components"], {})

    async def test_shared_settings_require_all_consumers(self):
        app = self.backend(engines={"one": SchemaEngine(1, 10), "two": SchemaEngine(5, 20)})
        project = await app.projects.acreate(config={"parameters": {"engines": {"shared": {'config': {'limit': 7}}}}})
        view = await project.aconfiguration()
        spec = view["schema"]["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]["shared"]
        self.assertIn("allOf", spec)
        Draft202012Validator(view["schema"]).validate(view["values"])
        for limit in (2, 15):
            invalid = deepcopy(view["values"])
            invalid["config"]["parameters"]["engines"]["shared"]["config"]["limit"] = limit
            self.assertFalse(Draft202012Validator(view["schema"]).is_valid(invalid))
            with self.assertRaises(ValueError):
                await project.asave(config=invalid["config"])
        # Registry에 나중에 추가한 소비자도 다음 저장부터 같은 검증기에 반영한다.
        app.engines.register("three", SchemaEngine(8, 9))
        with self.assertRaisesRegex(ValueError, "three"):
            await project.avalidate_configuration(view["project"]["config"])

    async def test_custom_configuration_receives_detached_values(self):
        class MutatingEngine(SchemaEngine):
            def configuration(self, config, name, *, session_config=None):
                config.parameters["engines"]["loop"]["config"]["completion"]["model"] = "changed"
                session_config["changed"] = True
                return {}
        app = self.backend(engines={"custom": MutatingEngine(1, 10)})
        project = await app.projects.acreate(config={"parameters": {"engines": {"loop": {'config': {'completion': {'model': 'original'}}}}}})
        session = await project.sessions.acreate(config={"data": {"purpose": "original"}})
        self.assertEqual(project.data.config.parameters["engines"]["loop"]["config"]["completion"]["model"], "original")
        self.assertNotIn("changed", session.data.config)
