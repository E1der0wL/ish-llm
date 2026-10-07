"""구현체 공통 외형·정책 소유권·UI 출처·실제 실행 인자를 함께 검사한다."""

import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from llm.core.models import ProjectConfig
from llm.core.schema import checked_implementation_schema
from llm.core.settings import SettingsLayout
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.components.memory import MemoryComponent
from llm.components.vision import VisionComponent
from llm.components.tools import ToolComponent
from llm.components.skills import SkillComponent
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.policies import CompletionPolicy, ExecutionLimitError


class SettingsContractTests(unittest.TestCase):
    def test_common_envelope_and_no_materialized_values(self):
        implementations = [LoopEngine(), GraphEngine(handlers={}),
            PreparationStep("prepare", lambda c: None), PipelineEngine([LoopEngine()]),
            RAGComponent(), MemoryComponent(), VisionComponent(), ToolComponent(), SkillComponent()]
        for item in implementations:
            with self.subTest(implementation=type(item).__name__):
                schema = checked_implementation_schema(item.configuration_schema())
                self.assertEqual(set(schema["properties"]), {"config", "policy"})
                validator = Draft202012Validator(schema)
                self.assertTrue(validator.is_valid({}))
                self.assertFalse(validator.is_valid({"timeout": 5}))
                self.assertFalse(validator.is_valid({"policy": None}))
                self.assertFalse(validator.is_valid({"config": None}))
        self.assertEqual(LoopEngine().configuration({}, "loop")["values"], {})
        self.assertEqual(GraphEngine(handlers={}).configuration({}, "graph")["values"], {})

    def test_project_session_agent_sources_and_limits_are_identical(self):
        project = ProjectConfig(parameters={"engines": {"writer": {
            "config": {"system_prompt": "project", "completion": {"timeout": 70}},
            "policy": {"request_timeout": 300, "completion": {"max_tokens": 100, "counter": "test"}},
        }}})
        session = {"parameters": {"engines": {"writer": {"policy": {"request_timeout": 60}}}}}
        engine = LoopEngine().for_agent({"engine": "writer", "system_prompt": None,
            "engine_options": {"policy": {"completion": {"max_tokens": 50}}}})
        view = engine.configuration(project, "writer", session_config=session)
        self.assertIsNone(view["values"]["config"]["system_prompt"])
        self.assertEqual(view["values"]["policy"]["request_timeout"], 60)
        self.assertEqual(view["values"]["config"]["completion"], {"timeout": 70})
        self.assertEqual(view["values"]["policy"]["completion"], {"max_tokens": 50, "counter": "test"})
        for path in ("/config/system_prompt", "/policy/completion/max_tokens"):
            self.assertEqual(view["sources"][path], "agent")
            self.assertTrue(view["editable"][path])
            schema = engine.configuration_schema()
            for part in path.strip("/").split("/"):
                schema = schema["properties"][part]
            self.assertNotIn("x-host-override", schema)
        self.assertEqual(view["sources"]["/policy/request_timeout"], "session")

    def test_misclassified_known_policy_and_flat_storage_rejected(self):
        for section in ({"type": "string"}, {"type": ["object", "null"]}):
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, "non-null object"):
                checked_implementation_schema({"type": "object", "properties": {"config": section}})
        for value in ({"request_timeout": 1}, {"config": None}, {"policy": None}):
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                ProjectConfig(parameters={"engines": {"loop": value}})
        for item, value in ((LoopEngine(), {"config": {"request_timeout": 1}}),
                            (RAGComponent(), {"config": {"embedding_concurrency": 2}}),
                            (VisionComponent(), {"config": {"provider": {"max_attempts": 2}}})):
            self.assertFalse(Draft202012Validator(item.configuration_schema()).is_valid(value))

    def test_component_client_source_and_snapshot_detachment(self):
        component = RAGComponent(embedding=EmbeddingModel(model="injected", timeout=7))
        project = SimpleNamespace(config=ProjectConfig(parameters={"components": {"rag": {
            "config": {"embedding_params": {"model": "project"}},
            "policy": {"provider": {"max_attempts": 2}},
        }}}))
        # No project files are needed for this pure resolution contract.
        component.configuration_layers = lambda p: [("project", p.config.parameters["components"]["rag"])]
        view = component.effective_configuration(project)
        self.assertEqual(view["sources"]["/config/embedding_params/timeout"], "client")
        self.assertEqual(view["sources"]["/config/embedding_params/model"], "project")
        self.assertEqual(view["model_providers"]["embedding"]["sources"]["/max_attempts"], "project")
        view["values"]["config"]["embedding_params"]["model"] = "changed"
        self.assertEqual(project.config.parameters["components"]["rag"]["config"]["embedding_params"]["model"], "project")

    def test_shared_policy_is_layer_independent_and_preserves_contract(self):
        import llm.policies as policies
        for file in Path(policies.__file__).parent.glob("*.py"):
            for node in ast.walk(ast.parse(file.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or "").startswith(("llm.services", "llm.engines", "llm.components")))
        request = {"model": "fake", "messages": [
            {"role": "system", "content": "rules"}, {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "current"}]}
        original = deepcopy(request)
        policy = CompletionPolicy(2, counter=lambda r: len(r["messages"]))
        self.assertEqual(policy.prepare(request)["messages"], [request["messages"][0], request["messages"][-1]])
        self.assertEqual(request, original)
        with self.assertRaises(ExecutionLimitError) as caught:
            CompletionPolicy(1, counter=lambda r: len(r["messages"])).prepare(request)
        self.assertEqual(caught.exception.code, "context_budget_exceeded")

    def test_layout_roundtrip_preserves_explicit_null_and_sdk_options(self):
        options = {"completion": {"timeout": None, "num_retries": 3, "custom": {"x": 1}},
                   "input_policy": None, "request_timeout": None}
        layout = LoopEngine.settings_layout
        self.assertEqual(layout.unpack(layout.pack(options)), options)
        self.assertEqual(layout.pack({}), {})
        self.assertEqual(layout.unpack({}), {})

    def test_pipeline_stage_effective_values_and_sources_are_project_owned(self):
        engine = PipelineEngine({"writer": LoopEngine()})
        config = ProjectConfig(parameters={"engines": {"pipeline": {"config": {"stages": {
            "writer": {"config": {"system_prompt": None}}}}}}})
        view = engine.configuration(config, "pipeline")
        self.assertIsNone(view["values"]["config"]["stages"]["writer"]["config"]["system_prompt"])
        path = "/config/stages/writer/config/system_prompt"
        self.assertEqual(view["sources"][path], "project")
        self.assertTrue(view["editable"][path])

    def test_nested_open_configuration_extensions_survive_resolution(self):
        component = MemoryComponent()
        value = {"config": {"processing": {"completion": {"future_extension": {"enabled": False}}}},
                 "policy": {"processing": {"timeout_seconds": None}}}
        component.validate_configuration(value)
        options = component.settings_layout.unpack(value)
        self.assertEqual(options["processing"], {"completion": {"future_extension": {"enabled": False}}, "timeout_seconds": None})
        self.assertEqual(component.settings_layout.pack(options), value)
        self.assertFalse(Draft202012Validator(component.configuration_schema()).is_valid(
            {"config": {"processing": {"timeout_seconds": 10}}}))

    def test_declared_settings_require_explicit_classification(self):
        with self.assertRaisesRegex(ValueError, "classification.*timeout"):
            SettingsLayout().schema({"type": "object", "properties": {"timeout": {"type": "number"}}})
        with self.assertRaisesRegex(ValueError, "belong to config/policy"):
            SettingsLayout(paths={"timeout": "timeout"})

    def test_internal_policy_names_cannot_enter_through_open_config(self):
        engine = LoopEngine()
        for value in (None, {"max_tokens": 10, "counter": "test"}):
            with self.subTest(value=value):
                supplied = {"config": {"input_policy": value}}
                self.assertFalse(Draft202012Validator(engine.configuration_schema()).is_valid(supplied))
                with self.assertRaisesRegex(ValueError, "declared config/policy"):
                    engine.settings_layout.unpack(supplied)
                with self.assertRaises(ValueError):
                    engine.configuration(ProjectConfig(parameters={"engines": {"loop": supplied}}), "loop")
        supplied = {"config": {"completion": {"input_policy": "provider-extension"}},
                    "policy": {"completion": None}}
        self.assertTrue(Draft202012Validator(engine.configuration_schema()).is_valid(supplied))
        self.assertEqual(engine.settings_layout.unpack(supplied),
                         {"completion": {"input_policy": "provider-extension"}, "input_policy": None})

    def test_layout_required_and_local_references_keep_their_meaning(self):
        layout = SettingsLayout(config=("connection", "peers"), policy=("limit",))
        original = {"type": "object", "additionalProperties": False, "required": ["connection", "limit"],
            "$defs": {"address": {"type": "object", "required": ["host"],
                                  "properties": {"host": {"type": "string", "minLength": 1}}}},
            "properties": {"connection": {"$ref": "#/$defs/address"},
                           "peers": {"type": "array", "items": {"$ref": "#/properties/connection"}},
                           "limit": {"type": "integer", "minimum": 1}}}
        before = deepcopy(original)
        schema = layout.schema(original)
        validator = Draft202012Validator(schema)
        valid = {"config": {"connection": {"host": "local"}, "peers": [{"host": "peer"}]}, "policy": {"limit": 1}}
        self.assertTrue(validator.is_valid(valid))
        for bad in ({}, {"policy": {"limit": 1}}, {"config": {"connection": {"host": "local"}}},
                    {**valid, "config": {"connection": {}, "peers": []}},
                    {**valid, "config": {"connection": {"host": "local"}, "peers": [{}]}}):
            with self.subTest(bad=bad):
                self.assertFalse(validator.is_valid(bad))
        self.assertEqual(original, before)
        self.assertEqual(layout.unpack(valid), {"connection": {"host": "local"}, "peers": [{"host": "peer"}], "limit": 1})

    def test_layout_rejects_constraints_it_cannot_relocate(self):
        layout = SettingsLayout(config=("value",))
        for extra in ({"allOf": [{"required": ["value"]}]}, {"dependentRequired": {"value": ["other"]}},
                      {"minProperties": 1}, {"$id": "urn:custom"}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "implementation_schema"):
                layout.schema({"type": "object", "properties": {"value": {"type": "string"}}, **extra})
        with self.assertRaisesRegex(ValueError, "overlap"):
            SettingsLayout(paths={"x": "config.x", "y": "config.x.y"})
        # A mapped subtree is copied whole, so its own conditions remain valid.
        spec = layout.schema({"type": "object", "properties": {"value": {"type": "object",
            "allOf": [{"required": ["a"]}], "properties": {"a": {"type": "integer"}}}}})
        self.assertFalse(Draft202012Validator(spec).is_valid({"config": {"value": {}}}))

    def test_nested_agent_metadata_matches_leaf_merge_including_empty_null_and_array(self):
        engine = LoopEngine().for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"extra_body": {"a": 1, "unset": None, "items": [1], "empty": {}}}})})
        config = ProjectConfig(parameters={"engines": {"loop": {"config": {"completion": {
            "extra_body": {"b": 2, "unset": 3, "items": [2], "empty": {"inherited": True}}}}}}})
        view = engine.configuration(config, "loop")
        self.assertNotIn("x-host-override", json.dumps(engine.configuration_schema()))
        for name in ("a", "unset", "items"):
            self.assertEqual(view["sources"]["/config/completion/extra_body/" + name], "agent")
            self.assertTrue(view["editable"]["/config/completion/extra_body/" + name])
        self.assertTrue(view["editable"]["/config/completion/extra_body/b"])
        self.assertTrue(view["editable"]["/config/completion/extra_body/empty/inherited"])

    def test_pipeline_preserves_child_reference_scope(self):
        class RefEngine:
            settings_name = None
            def configuration_schema(self):
                return SettingsLayout(config=("address",)).schema({"type": "object",
                    "$defs": {"address": {"type": "string", "minLength": 1}},
                    "properties": {"address": {"$ref": "#/$defs/address"}}})
            def configuration(self, config, name, *, session_config=None):
                from llm.core.configuration import engine_configuration
                return engine_configuration(config, self.settings_name or name,
                    session_config=session_config, schema=self.configuration_schema())
            async def execute(self, context):
                yield
        engine = PipelineEngine({"nested": PipelineEngine({"call": RefEngine()})})
        settings = {"config": {"stages": {"nested": {"config": {"stages": {"call": {"config": {"address": "host"}}}}}}}}
        config = ProjectConfig(parameters={"engines": {"pipeline": settings}})
        self.assertEqual(engine.configuration(config, "pipeline")["values"], settings)
        settings["config"]["stages"]["nested"]["config"]["stages"]["call"]["config"]["address"] = ""
        self.assertFalse(Draft202012Validator(engine.configuration_schema()).is_valid(settings))

    def test_mapped_subtree_not_constraint_is_not_replaced_by_policy_guard(self):
        layout = SettingsLayout(config=("options",), paths={"policy_options": "policy.options"})
        schema = layout.schema({"type": "object", "properties": {
            "options": {"type": "object", "not": {"required": ["forbidden"]}},
            "policy_options": {"type": "object", "properties": {"limit": {"type": "integer"}}}}})
        validator = Draft202012Validator(schema)
        self.assertFalse(validator.is_valid({"config": {"options": {"forbidden": 1}}}))
        self.assertTrue(validator.is_valid({"config": {"options": {"limit": 1}}}))
