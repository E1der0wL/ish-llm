"""닫힌 backend 계약과 선택 구현체/metadata 소유 경계를 검증한다."""

from copy import deepcopy
import unittest
import ast
import tempfile
from pathlib import Path
from copy import copy
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from llm.core.schema import object_schema, metadata_schema
from llm.components.agents import AgentComponent
from llm.components.skills import SkillComponent
from llm.components.prompts import PromptComponent
from llm.components.goals import GoalComponent
from llm.components.mcp import MCPComponent
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.workflows.graph import validate_handler_options
from llm.components.refinement import RefinementComponent
from llm.components.memory import MemoryComponent
from llm.components.rag import RAGComponent, EmbeddingModel
from llm.core.models import ProjectConfig, RunStatus
from llm.llm import LargeLanguageModel


class DefinitionOwnershipTests(unittest.TestCase):
    def test_closed_definitions_and_explicit_application_metadata(self):
        samples = [(AgentComponent(), {"purpose": "review", "engine": "loop"}),
                   (SkillComponent(), {"instructions": "Check evidence"}),
                   (PromptComponent(), {"messages": [{"role": "system", "content": "Review"}]}),
                   (GoalComponent(), {"title": "work", "objective": "verify", "scope": {"type": "project"}, "status": "active"}),
                   (MCPComponent(), {"transport": "stdio", "command": "server"})]
        for component, value in samples:
            with self.subTest(component=component.name):
                component.validate_record("test", value)
                component.validate_record("test", {**value, "metadata": {
                    "ui": {}, "application": {"anything": True}, "tools": ["admin"], "engine": "other"}})
                for key in ("foo", "ui", "system_promt"):
                    with self.assertRaisesRegex(ValueError, key):
                        component.validate_record("test", {**value, key: {}})
        for key in ("tool", "resource", "policies", "loop", "skills", "rag", "mcp"):
            with self.assertRaises(ValueError):
                AgentComponent().validate_record("test", {"purpose": "review", "engine": "loop", key: {}})

    def test_graph_agent_allowlist_and_closed_settings(self):
        engine = GraphEngine(handlers={})
        profile = {"purpose": "review", "description": "alternate environment", "engine": "review",
                   "engine_options": {"workflow": "flow"}, "metadata": {"ui": {}, "tools": ["admin"]}}
        bound = engine.for_agent(profile)
        self.assertEqual(bound.workflow, "flow")
        for key in ("foo", "system_promt", "instructions", "permissions", "resources2", "future_option", "ui",
                    "completion", "system_prompt", "tools", "resources", "policy", "input_schema", "output_schema", "output_format"):
            for value in ({}, [], None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    engine.for_agent({**profile, key: value})
        for options in ({"foo": 1}, {"config": {"foo": 1}}, {"config": {"revision": "x"}},
                        {"config": {"settings_name": "other"}}, {"policy": {"foo": 1}}):
            with self.subTest(options=options), self.assertRaises((ValueError, ValidationError)):
                engine.for_agent({**profile, "engine_options": {"workflow": "flow", **options}})

    def test_schema_helper_is_closed_without_inventing_values(self):
        self.assertFalse(Draft202012Validator(object_schema()).is_valid({"future": True}))
        self.assertTrue(Draft202012Validator(metadata_schema()).is_valid({"future": True}))
        self.assertEqual(object_schema()["properties"], {})

    def test_skill_resources_are_references_not_authority(self):
        component = SkillComponent()
        component.validate_record("x", {"instructions": "read", "resources": [
            {"uri": "manual.md", "description": "reference", "metadata": {"tool": "not authority"}}]})
        for resource in ({"tools": ["admin"]}, {"uri": "manual.md", "engine": "other"}):
            with self.assertRaises(ValueError):
                component.validate_record("x", {"instructions": "read", "resources": [resource]})

    def test_workflow_controls_are_closed_and_metadata_explicit(self):
        value = WorkflowGraph(entry="end", metadata={"ui": {}}).node("end", "end").to_dict()
        for changed in ({**value, "ui": {}}, {**value, "nodes": {"end": {"type": "end", "tools": []}}}):
            with self.assertRaises(ValueError):
                WorkflowComponent().validate_record("flow", changed)
        proposal = {"target": {"component": "skills", "identifier": "review"}, "operation": "create",
                    "expected_version": None, "reason": "review", "evidence": [{"session_id": "s", "run_id": "r"}],
                    "patch": {"instructions": "review"}, "status": "proposed", "before": {},
                    "source": {"kind": "api"}, "history": []}
        validator = Draft202012Validator(RefinementComponent.schema)
        validator.validate(proposal)
        for key in ("foo", "status2"):
            self.assertFalse(validator.is_valid({**proposal, key: None}))

    def test_project_unknown_sections_and_policies_are_rejected(self):
        for value in ({"ui": {}}, {"parameters": {"future": {}}}, {"policies": {"future": {}}}):
            with self.assertRaises(ValueError):
                ProjectConfig(value)
        config = ProjectConfig(data={"ui": {}, "timeout": 1, "engine": "not selected"})
        self.assertEqual(config.parameters, {})
        self.assertEqual(config.policies, {})

    def test_selected_handler_owns_additional_fields(self):
        class Handler:
            @staticmethod
            def configuration_schema():
                return object_schema({"new_option": {"type": "integer", "minimum": 1}}, required=["new_option"])
        validate_handler_options({"type": "custom", "new_option": 8, "metadata": {"timeout": 1}}, Handler())
        for value in ({"type": "custom", "new_option": "bad"}, {"type": "custom", "new_option": 8, "typo": True}):
            with self.assertRaises(ValueError):
                validate_handler_options(value, Handler())

    def test_production_open_schemas_have_declared_owners(self):
        """실제 공개 스키마를 순회한다. not의 조건 fragment는 설정 객체가 아니다."""
        from llm.components.vision import VisionComponent
        from llm.components.tools import ToolComponent
        from llm.engines.loop import LoopEngine
        from llm.core.policies import policy_schema
        schemas = [policy_schema(), LoopEngine().configuration_schema(), GraphEngine(handlers={}).configuration_schema(),
                   LoopEngine().for_agent({"engine": 'loop', "engine_options": LoopEngine.settings_layout.pack({'completion': {"extra_body": {"adapter_private": 3}}})}).configuration_schema(),
                   MemoryComponent.content_schema]
        for component in (AgentComponent(), SkillComponent(), PromptComponent(), GoalComponent(), MCPComponent(),
                          WorkflowComponent(), RefinementComponent(), RAGComponent(), MemoryComponent(), VisionComponent(), ToolComponent()):
            schemas.append(component.configuration_schema())
            if hasattr(component, "schema"):
                schemas.append(component.schema)
        def visit(spec, path):
            if not isinstance(spec, dict):
                return
            kinds = spec.get("type", [])
            if kinds == "object" or isinstance(kinds, list) and "object" in kinds:
                if spec.get("additionalProperties", True) is True:
                    self.assertTrue(spec.get("x-schema-owner"), path)
                    self.assertIn(spec.get("x-open-kind"), {"metadata", "provider", "implementation", "adapter", "result", "schema", "data"}, path)
            for key, value in spec.get("properties", {}).items():
                visit(value, path + "." + key)
            for key in ("items", "additionalProperties"):
                visit(spec.get(key), path + "." + key)
            for key in ("allOf", "anyOf", "oneOf"):
                for i, value in enumerate(spec.get(key, [])):
                    visit(value, f"{path}.{key}[{i}]")
        for i, spec in enumerate(schemas):
            with self.subTest(schema=i):
                visit(spec, str(i))

    def test_no_accidental_bare_object_schema_literals(self):
        root = Path(__file__).resolve().parents[2] / "llm"
        unowned = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for definition in ast.walk(tree):
                if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(definition):
                    if not isinstance(node, ast.Dict):
                        continue
                    values = {key.value: value for key, value in zip(node.keys, node.values) if isinstance(key, ast.Constant)}
                    kind = values.get("type")
                    if not isinstance(kind, ast.Constant) or kind.value != "object":
                        continue
                    if "additionalProperties" not in values:
                        # not/anyOf 아래 key 존재 여부를 검사하는 조건식이며 설정 계약이 아니다.
                        if path.relative_to(root).as_posix() == "core/settings.py" and definition.name == "_presence":
                            continue
                        unowned.append(f"{path.relative_to(root)}:{definition.name}:{node.lineno}")
        self.assertEqual(unowned, [])


class OwnershipIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_handler_options_need_no_parent_changes(self):
        seen = []
        class Handler:
            @staticmethod
            def configuration_schema():
                return object_schema({"new_option": {"type": "integer"}}, required=["new_option"])
            async def __call__(self, node):
                seen.append(node.definition["new_option"])
                return {"result": node.definition["new_option"]}
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={"graph": GraphEngine(handlers={"custom": Handler()})}) as backend:
                project = await backend.projects.acreate("custom", components=["workflows"])
                workflow = (WorkflowGraph(entry="a", metadata={"engine": "ignored"})
                    .node("a", "custom", new_option=7, metadata={"tools": ["ignored"]})
                    .node("end", "end").connect("a", "end").to_dict())
                await project.components.workflows.acreate(workflow, identifier="test")
                session = await project.sessions.acreate()
                result = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "test"})).wait()
                self.assertEqual(result.data.status, RunStatus.COMPLETED, result.data.error)
                self.assertEqual(seen, [7])
                workflow["nodes"]["a"]["new_option"] = "bad"
                await project.components.workflows.asave("test", workflow)
                rejected = await (await session.run.submit("go", engine="graph", engine_options={"workflow": "test"})).wait()
                self.assertEqual(rejected.data.status, RunStatus.FAILED)
                self.assertIn("new_option", rejected.data.error)
                self.assertEqual(seen, [7])

    async def test_rag_delegates_unknown_child_fields_without_interpreting_them(self):
        class Child:
            params = {}
            @staticmethod
            def configuration_schema():
                return object_schema({"dialect": {"enum": ["alpha", "beta"]}})
            def configured(self, params):
                Draft202012Validator(self.configuration_schema()).validate(params)
                clone = copy(self)
                clone.params = deepcopy(params)
                return clone
            def identity(self, **kwargs):
                return "custom-model"
            async def embed(self, texts, **kwargs):
                return {"data": [{"embedding": [1 if self.params["dialect"] == "beta" else 2, 3]}]}
        component = RAGComponent(embedding=Child())
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[component]) as backend:
                project = await backend.projects.acreate("child", components=["rag"], config=ProjectConfig(parameters={
                    "components": {"rag": {"config": {"embedding_params": {"dialect": "beta"}}}}}))
                worker = component.configured(project.data)
                self.assertEqual(await worker.embed(["text"]), [[1, 3]])
                with self.assertRaises(ValueError):
                    component.validate_configuration({"config": {"embedding_params": {"api_base": "parent-must-not-accept-this"}}})

    async def test_provider_specific_options_survive_parent_boundary(self):
        calls = []
        async def call(**kwargs):
            calls.append(deepcopy(kwargs))
            return {"data": [{"embedding": [1, 2]}]}
        component = RAGComponent(embedding=EmbeddingModel(model="custom/model", embedding_fn=call))
        opaque = {"custom_route": {"future_sdk_flag": [True, "opaque"]}}
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[component]) as backend:
                project = await backend.projects.acreate("provider", components=["rag"], config=ProjectConfig(parameters={
                    "components": {"rag": {"config": {"embedding_params": opaque}}}}))
                await component.configured(project.data).embed(["text"])
        self.assertEqual(calls[0]["custom_route"], opaque["custom_route"])
        self.assertNotIn("timeout", calls[0])
        self.assertEqual(calls[0]["cache"], {"no-cache": True, "no-store": True})

    async def test_memory_metadata_cannot_select_consolidation_targets(self):
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[MemoryComponent()]) as backend:
                project = await backend.projects.acreate("memory", components=["memory"])
                memory = project.components.memory
                await memory.acreate({"content": "old"}, identifier="old")
                await memory.acreate({"content": "candidate", "status": "candidate", "metadata": {
                    "replaces": [{"id": "old", "revision": 1}]}}, identifier="proposal")
                await memory.aconsolidate("proposal", expected_revision=1)
                self.assertFalse((await memory.aload("old"))["deleted"])
                self.assertEqual((await memory.aload("old"))["revision"], 1)
