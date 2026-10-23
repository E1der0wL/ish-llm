"""Shared contracts work without selecting or interpreting a built-in owner."""

from llm.core.model_protocol import ModelRequest, ModelMessage, ContentPart, ContentKind

import tempfile
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from pathlib import Path
from copy import deepcopy

from llm.llm import LargeLanguageModel
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.core.results import EngineDelta, EngineOutput, ResumeOutput
from llm.core.checkpoints import select_checkpoint, build_envelope, unwrap_record
from llm.core.configuration import resolve_engine_config
from llm.core.schema import checked_implementation_schema, implementation_schema, object_schema
from llm.core.contracts import JsonValue
from llm.core.models import ProjectConfig
from llm.services.runtime.usage import ComponentUsage
from llm.providers.observations import ModelCall, model_observer, observed_call
from llm.policies import ExecutionLimitError
from llm.components.processing import CompletionPipeline, CompletionMessage, CompletionRequest
from llm.core.contracts import Diagnostic


class ResumeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_engine_receives_raw_partial_output_and_owning_step(self):
        received = []

        class CustomEngine(BaseEngine):
            checkpoint_name = "custom"

            def validate_resume(self, checkpoint, **options):
                self.assert_header = checkpoint["header"]["input_message_id"]

            async def execute(self, context):
                if context.checkpoint is None:
                    yield EngineEvent(EngineEventType.CHECKPOINT, metadata={
                        "name": "custom", "operation": "initialize", "header": {
                            "input_message_id": context.run.input_message_id,
                            "message_ids": [m.id for m in context.messages]}})
                    yield EngineEvent(EngineEventType.STEP_STARTED, step_id="1" * 32, kind="analysis",
                                      metadata={"owner_round": "phase-a"})
                    yield EngineEvent(EngineEventType.TEXT_DELTA, step_id="1" * 32,
                        delta=EngineDelta("custom-output", "unfinished", step_id="1" * 32))
                    raise RuntimeError("interrupted custom work")
                received.extend(context.resume_outputs)
                yield EngineEvent(EngineEventType.OUTPUT, output=EngineOutput(text="done"))

        with tempfile.TemporaryDirectory() as root:
            app = LargeLanguageModel(root)
            app.engines.register("custom", CustomEngine())
            project = await app.projects.acreate("custom", components=[], conversation_storage="file")
            await project.agents.acreate({"engine": "custom"}, identifier="custom")
            session = await project.sessions.acreate("custom", agent_ids=sorted(await project.agents.alist()))
            try:
                first = (await (await session.run.submit("go", recipients=["custom"])).wait())[0]
                self.assertEqual(first.data.status, "failed")
                self.assertIn("interrupted custom work", first.data.error)
                checkpoint = await first.acheckpoint()
                self.assertEqual(checkpoint["engine"], "custom")
                outputs = await first.aoutputs()
                self.assertTrue(any(o.text == "unfinished" for o in outputs), outputs)
                second = (await (await session.run.resume(first.id, agent_id="custom", partial_output=True)).wait())[0]
                self.assertEqual(second.data.status, "completed", second.data.error)
                self.assertEqual(len(received), 1)
                item = received[0]
                self.assertIsInstance(item, ResumeOutput)
                self.assertEqual(item.run_id, first.id)
                self.assertEqual(item.output.text, "unfinished")
                self.assertEqual(item.step.kind, "analysis")
                self.assertEqual(item.step.metadata["owner_round"], "phase-a")
                self.assertNotIn("iteration", item.output.metadata)
                self.assertEqual(await first.aoutputs(), outputs)
            finally:
                await app.shutdown()

    def test_checkpoint_selection_requires_an_unambiguous_stored_name(self):
        self.assertEqual(select_checkpoint(["custom"]), "custom")
        self.assertEqual(select_checkpoint(["loop"]), "loop")
        self.assertEqual(select_checkpoint(["custom", "other"], "other"), "other")
        self.assertEqual(select_checkpoint([], "custom"), "custom")
        for names in ([], ["custom", "other"]):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, "Specify"):
                select_checkpoint(names)


class ConfigurationBoundaryTests(unittest.TestCase):
    def test_nested_record_envelope_has_no_graph_dependency(self):
        payload = {"kind": "instruction", "status": "input", "target_scope": "custom-owner", "message_ids": []}
        record = {**build_envelope(engine_scope="custom-owner", checkpoint_name="custom-checkpoint",
            local_key="custom-key", status="completed", container=True, requires_retry=False), "payload": payload}
        self.assertEqual(unwrap_record(record), (payload, "custom-owner"))
        self.assertEqual(unwrap_record(payload), (payload, None))
        from llm.services.runtime.steering import instruction_records
        self.assertEqual([(key, value.to_dict()) for key, value in
                          instruction_records({"records": {"key": record}})], [("key", payload)])
        for key, value in (("engine_scope", ""), ("requires_retry", True), ("payload", [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                unwrap_record({**record, key: value})

    def test_direct_limit_inherits_narrows_and_rejects_null_or_widening(self):
        schema = implementation_schema(policy=object_schema({"quota": {
            "type": ["integer", "null"], "minimum": 1, "x-narrowing": "maximum"}}))
        config = ProjectConfig(parameters={"engines": {"custom": {"policy": {"quota": 8}}}})
        self.assertEqual(resolve_engine_config(config, "custom", schema=schema)["values"]["policy"]["quota"], 8)
        self.assertEqual(resolve_engine_config(config, "custom", agent={"policy": {"quota": 4}},
                                              schema=schema)["values"]["policy"]["quota"], 4)
        for value in (9, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "widens"):
                resolve_engine_config(config, "custom", agent={"policy": {"quota": value}}, schema=schema)

    def test_narrowing_cannot_hide_in_refs_or_composed_schemas(self):
        limit = {"type": "integer", "x-narrowing": "maximum"}
        candidates = [
            implementation_schema(policy=object_schema({"quota": {"$ref": "#/$defs/limit"}}),
                                  **{"$defs": {"limit": limit}}),
            *[implementation_schema(policy=object_schema({"quota": {key: [limit]}}))
              for key in ("allOf", "anyOf", "oneOf")],
            implementation_schema(policy=object_schema({"quotas": {"type": "array", "items": limit}})),
        ]
        for schema in candidates:
            with self.subTest(schema=schema):
                for check in (checked_implementation_schema,
                              lambda s: resolve_engine_config({}, "custom", schema=s)):
                    with self.assertRaisesRegex(ValueError, "x-narrowing"):
                        check(schema)

    def test_normal_json_schema_composition_and_data_annotations_still_work(self):
        schema = implementation_schema(config=object_schema({"value": {
            "allOf": [{"type": "integer"}, {"minimum": 1}],
            "examples": [{"x-narrowing": "not a schema annotation"}]}}))
        checked_implementation_schema(schema)
        resolve_engine_config({"parameters": {"engines": {"custom": {"config": {"value": 2}}}}},
                              "custom", schema=schema)

    def test_native_union_and_fixed_and_variadic_tuples_roundtrip_without_coercion(self):
        @dataclass(frozen=True, slots=True)
        class Data(JsonValue):
            optional: str | None
            pair: tuple[str, int]
            items: tuple[int, ...]

        for value in (None, "text"):
            item = Data(value, ("a", 3), (1, 2, 3))
            self.assertEqual(Data.from_dict(item.to_dict()), item)
        for pair in (("a", "3"), ("a", 3, 4)):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                Data(None, pair, ())


class ModelObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_processing_rejects_unsupported_representation_before_callback(self):
        seen = []
        message = CompletionMessage(ModelMessage("user", (ContentPart(ContentKind.TEXT, "instruction"),)), "current")
        request = CompletionRequest({}, [message], 0)
        with self.assertRaisesRegex(ValueError, "string current instruction"):
            CompletionPipeline._validate(request, [message], ModelRequest(), "current")
        with self.assertRaisesRegex(ValueError, "string current instruction"):
            await CompletionPipeline.recover(request.to_model(), Diagnostic("custom", "too large"), 1,
                current_index=0, decide=lambda repair: seen.append(repair))
        self.assertEqual(seen, [])

    async def test_custom_adapter_accounts_opaque_requests_and_responses(self):
        entries, executed, counted = [], [], []
        with tempfile.TemporaryDirectory() as root:
            def save_usage(project, entry):
                index = next((i for i, value in enumerate(entries) if value["id"] == entry["id"]), None)
                if index is None:
                    entries.append(deepcopy(entry))
                else:
                    entries[index] = deepcopy(entry)
            component = SimpleNamespace(name="custom", root=lambda p: Path(root),
                model_usage=lambda p: list(entries), save_model_usage=save_usage)
            project = SimpleNamespace(id="custom_project", config=ProjectConfig(policies={"usage": {
                "project_max_tokens": 10, "counter": "custom"}}))

            class Data:
                registry = SimpleNamespace(names=lambda: ("custom",), get=lambda n: component,
                                           stored=lambda project: ("custom",))
                def _current(self):
                    return project, component
                async def _async_call(self, operation, *args):
                    return operation(*args)

            observer = ComponentUsage(Data(), SimpleNamespace(run_repository=object(), list=lambda p, **kw: []),
                                      {"custom": lambda value: counted.append(value) or 8})
            request = {"unfamiliar_input": ["abc"]}
            async def call(**kwargs):
                executed.append(kwargs)
                return {"unfamiliar_usage": 4}
            invocation = ModelCall("custom.score", "custom-model", lambda counter: counter(request["unfamiliar_input"]),
                lambda response: {"total_tokens": response["unfamiliar_usage"]}, lambda error: "custom_error")
            with model_observer(observer):
                self.assertEqual(await observed_call(invocation, request, call), {"unfamiliar_usage": 4})
                with self.assertRaises(ExecutionLimitError) as error:
                    await observed_call(invocation, request, call)
                self.assertEqual(error.exception.code, "usage_limit")
            self.assertEqual(executed, [request])
            self.assertEqual(counted, [["abc"], ["abc"]])
            self.assertEqual(entries[0]["reserved_tokens"], 8)
            self.assertEqual(entries[0]["usage"], {"total_tokens": 4})
            self.assertTrue(entries[0]["usage_complete"])

            # No token policy means no reservation work, but calls remain recorded.
            project.config = ProjectConfig()
            with model_observer(observer):
                await observed_call(invocation, request, call)
                failure = RuntimeError("custom failure")
                async def fail(**kwargs):
                    raise failure
                with self.assertRaises(RuntimeError) as raised:
                    await observed_call(invocation, request, fail)
                self.assertIs(raised.exception, failure)
            self.assertEqual(len(entries), 3)
            self.assertIsNone(entries[1]["reserved_tokens"])
            self.assertEqual(entries[2]["diagnostic_code"], "custom_error")
            self.assertEqual(counted, [["abc"], ["abc"]])
