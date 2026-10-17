"""SDK formats, resolver identities and JSON instance data stay with their owners."""

import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from llm.components.base import Component
from llm.components.registry import ComponentRegistry
from llm.components.tools.resolver import ComponentToolResolver
from llm.core.models import ProjectConfig, now
from llm.core.parameters import ParameterLayout
from llm.core.results import CompletionResult
from llm.core.schema import checked_schema, implementation_schema, object_schema, schema_nodes
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from llm.llm import LargeLanguageModel
from llm.providers.streaming import CompletionUpdate, CompletionError
from llm.services.runtime.runs import RunManager
from llm.services.schema import describe_project_config


class SchemaTraversalTests(unittest.TestCase):
    def test_keyword_property_names_are_data_not_schema_keywords(self):
        names = ("$ref", "$dynamicRef", "default", "const", "enum", "examples", "properties")
        schema = object_schema({name: {"type": "string"} for name in names})
        self.assertEqual(checked_schema(schema), schema)
        for name in names:
            with self.subTest(name=name):
                invalid = object_schema({name: {"type": "string", "default": "hidden"}})
                with self.assertRaisesRegex(ValueError, "implicit defaults"):
                    checked_schema(invalid)
                invalid = object_schema({name: {"$ref": "https://invalid.example/schema"}})
                with self.assertRaisesRegex(ValueError, "Only local"):
                    checked_schema(invalid)

    def test_only_schema_locations_are_visited(self):
        literal = {"default": "data", "$ref": "not a reference", "x-resource": "token_counters"}
        schema = {"allOf": [True, {"const": literal}], "examples": [literal],
                  "x-application": literal, "$defs": {"named": {"type": "string"}},
                  "dependentSchemas": {"present": {"properties": {"x": {"type": "integer"}}}},
                  "prefixItems": [{"type": "boolean"}]}
        self.assertEqual(checked_schema(schema), schema)
        self.assertFalse(any(node is literal for node in schema_nodes(schema)))
        for location in ("$defs", "dependentSchemas", "patternProperties"):
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, "implicit defaults"):
                checked_schema({location: {"example": {"default": 1}}})

    def test_ui_projection_preserves_literals_and_owner_constraints(self):
        literal = {"x-resource": "token_counters", "default": "data", "$ref": "ordinary text"}
        spec = implementation_schema(config=object_schema({
            "payload": {"const": literal, "examples": [literal]},
            "counter": {"type": "string", "enum": ["alpha", "absent"], "x-resource": "token_counters"},
            "unavailable": {"type": "string", "enum": ["absent"], "x-resource": "token_counters"},
        }))
        original = deepcopy(spec)
        class Engine:
            async def execute(self, context):
                if False:
                    yield
            def describe_config(self):
                return spec
        engines = EngineRegistry()
        engines.register("custom", Engine())
        app = SimpleNamespace(engines=engines, project_manager=SimpleNamespace(components=ComponentRegistry()),
                              policy_resolver=SimpleNamespace(token_counters={"alpha": lambda r: 1, "beta": lambda r: 1}))
        schema = describe_project_config(app)
        projected = schema["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]["custom"]
        fields = projected["properties"]["config"]["properties"]
        self.assertEqual(spec, original)
        self.assertEqual(fields["payload"], original["properties"]["config"]["properties"]["payload"])
        self.assertEqual(fields["counter"]["enum"], ["alpha"])
        validate = Draft202012Validator(projected)
        self.assertTrue(validate.is_valid({"config": {"payload": literal, "counter": "alpha"}}))
        self.assertFalse(validate.is_valid({"config": {"counter": "beta"}}))
        self.assertFalse(validate.is_valid({"config": {"unavailable": "absent"}}))

    def test_parameter_layout_relocates_only_schema_references(self):
        literal = {"$ref": "#/properties/value", "$id": "ordinary data", "default": 7}
        source = object_schema({
            "value": {"type": "string"},
            "alias": {"$ref": "#/properties/value"},
            "payload": {"const": literal, "examples": [literal]},
            "$ref": {"type": "string"},
        })
        source["x-application"] = literal
        layout = ParameterLayout(config=("value", "alias", "payload", "$ref"))
        result = checked_schema(layout.schema(source))
        fields = result["properties"]["config"]["properties"]
        self.assertEqual(fields["alias"]["$ref"], "#/properties/config/properties/value")
        self.assertEqual(fields["payload"], source["properties"]["payload"])
        self.assertEqual(result["x-application"], literal)
        self.assertEqual(fields["$ref"], {"type": "string"})
        self.assertTrue(Draft202012Validator(result).is_valid({"config": {
            "alias": "text", "payload": literal, "$ref": "text"}}))
        self.assertFalse(Draft202012Validator(result).is_valid({"config": {"alias": 3}}))
        invalid = deepcopy(source)
        invalid["properties"]["value"]["$id"] = "schema-scope"
        with self.assertRaisesRegex(ValueError, "resource scopes"):
            layout.schema(invalid)


class StreamingBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_decoder_uses_opaque_chunks_and_final_message(self):
        seen = []
        class Decoder:
            def __init__(self, **limits):
                seen.append(limits)
            def decode(self, chunk):
                yield CompletionUpdate(text=chunk[0], reasoning=chunk[1], usage={"total_tokens": 3},
                                       finish_reason="custom-done")
            def finish(self):
                return {"custom_message": "preserved"}
        engine = BaseEngine(completion_fn=lambda **kw: iter([("answer", "reason")]), completion_decoder=Decoder)
        response = {}
        events = [event async for event in engine.stream_completion({}, response=response, max_output_chars=20)]
        self.assertEqual(response, {"custom_message": "preserved"})
        self.assertEqual([item for item in events if isinstance(item, str)], ["answer"])
        result = [item.completion for item in events if isinstance(item, EngineEvent)][-1]
        self.assertEqual((result.status, result.reasoning_content, result.finish_reason),
                         ("completed", "reason", "custom-done"))
        self.assertEqual(result.usage, {"total_tokens": 3})
        self.assertEqual(seen, [{"max_tool_calls": None, "max_argument_chars": None, "max_output_chars": 20}])

    async def test_decoder_failure_after_receipt_never_replays_or_publishes_message(self):
        calls = []
        class Decoder:
            def __init__(self, **limits):
                pass
            def decode(self, chunk):
                yield CompletionUpdate(reasoning="partial")
                raise CompletionError("invalid opaque payload")
            def finish(self):
                raise AssertionError("cannot finalize")
        def provider(**kw):
            calls.append(kw)
            yield object()
        response, events = {"previous": "value"}, []
        engine = BaseEngine(completion_fn=provider, completion_decoder=Decoder)
        with self.assertRaises(CompletionError):
            async for event in engine.stream_completion({}, response=response, provider={"max_attempts": 3}):
                events.append(event)
        self.assertEqual(len(calls), 1)
        self.assertEqual(response, {"previous": "value"})
        self.assertEqual(events[-1].completion.status, "failed")
        self.assertEqual(events[-1].completion.reasoning_content, "partial")

    async def test_invalid_final_message_never_replaces_previous_response(self):
        class Uncopyable:
            def __deepcopy__(self, memo):
                raise TypeError("invalid adapter message")
        for message in ([], {"invalid": Uncopyable()}):
            with self.subTest(message_type=type(message)):
                class Decoder:
                    def __init__(self, **limits):
                        pass
                    def decode(self, chunk):
                        yield CompletionUpdate(text="partial")
                    def finish(self):
                        return message
                engine = BaseEngine(completion_fn=lambda **kw: iter([object()]), completion_decoder=Decoder)
                response = {"previous": "preserved"}
                with self.assertRaises(TypeError):
                    async for _ in engine.stream_completion({}, response=response):
                        pass
                self.assertEqual(response, {"previous": "preserved"})


class ResolverUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapped_resolver_counts_disabled_component_receipts(self):
        class Auxiliary(Component):
            name, directory = "auxiliary", "auxiliary"
        for key in ("project_max_calls", "project_max_tokens", "max_calls", "max_tokens"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                effects = []
                class Engine(BaseEngine):
                    async def run(self, context):
                        component.save_model_usage(context.project, {
                            "id": "receipt", "component": "auxiliary", "operation": "custom", "model": "custom",
                            "started_at": now(), "status": "completed", "usage_complete": True,
                            "usage": {"total_tokens": 4}, "reserved_tokens": 4, "run_id": context.run.id})
                        yield EngineEvent(EngineEventType.COMPLETION, completion=CompletionResult(reserved_tokens=2))
                        effects.append("provider invoked")
                component = Auxiliary()
                async with LargeLanguageModel(root, components=[component], engines={"custom": Engine()}) as app:
                    policy = {key: 1 if key.endswith("calls") else 5}
                    if key.endswith("tokens"):
                        policy["counter"] = "model_default"
                    project = await app.projects.acreate(components=[], config=ProjectConfig(policies={"usage": policy}))
                    session = await project.sessions.acreate()
                    delegate = ComponentToolResolver(app.project_manager.components)
                    read = []
                    class Wrapped:
                        def resolve(self, project, names):
                            return delegate.resolve(project, names)
                        def model_usage(self, project):
                            read.append(True)
                            return delegate.model_usage(project)
                    manager = RunManager(app.project_manager.sessions, app.engines, session=session.data,
                                         capabilities=Wrapped(), repository=app.run_repository, steps=app.step_manager)
                    try:
                        await manager.submit("test", engine="custom")
                        await manager.wait_idle()
                        run = manager.repository.list(session.data)[0]
                        self.assertEqual(run.error_code, "usage_limit", run.error)
                        self.assertEqual(effects, [])
                        self.assertEqual(read, [True])
                    finally:
                        await manager.shutdown()

    async def test_missing_usage_contract_fails_closed_only_when_limited(self):
        for limited in (False, True):
            with self.subTest(limited=limited), tempfile.TemporaryDirectory() as root:
                effects = []
                class Engine(BaseEngine):
                    async def run(self, context):
                        yield EngineEvent(EngineEventType.COMPLETION, completion=CompletionResult())
                        effects.append(True)
                class Resolver:
                    def resolve(self, project, names):
                        return {}
                async with LargeLanguageModel(root, engines={"custom": Engine()}) as app:
                    project = await app.projects.acreate(config=ProjectConfig(
                        policies={"usage": {"max_calls": 1}} if limited else {}))
                    session = await project.sessions.acreate()
                    manager = RunManager(app.project_manager.sessions, app.engines, session=session.data,
                                         capabilities=Resolver(), repository=app.run_repository, steps=app.step_manager)
                    try:
                        await manager.submit("test", engine="custom")
                        await manager.wait_idle()
                        run = manager.repository.list(session.data)[0]
                        self.assertEqual(run.error_code, "usage_configuration" if limited else None, run.error)
                        self.assertEqual(effects, [] if limited else [True])
                    finally:
                        await manager.shutdown()
