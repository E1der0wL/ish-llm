from llm.core.schema import implementation_schema
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.configuration_fixtures import rag_project, rag_settings
"""운영 API의 사전 검증, 충돌, 중단 복구와 UI 스키마 계약을 검증한다."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig, Tool, ToolRegistry, ToolComponent, ToolContract, ServiceConfig
from llm.components.base import Component
from llm.components.memory import MemoryComponent
from llm.components.rag import RAGComponent, EmbeddingModel, TripleExtractor
from llm.core.models import MessageStatus
from llm.services.runtime.tools import ToolPolicy, ToolCall
from llm.services.runtime.operations import operation_token
from llm.services.infrastructure.storage import atomic_json, read_json
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class OperationsSchemaTests(unittest.IsolatedAsyncioTestCase):
    def operation_call(self, app, project, session, arguments):
        """외부 확인 테스트도 실제 소유 Run/Tool Step을 가진 원장을 사용한다."""
        from llm.core.models import Run, RunStatus, new_id
        identifier = new_id()
        run = Run(identifier, session.id, new_id(), new_id(), "custom",
                  app.run_repository.paths(session.data, identifier))
        run.status = RunStatus.INTERRUPTED
        with app.project_manager.ownership.scope():
            app.run_repository.save(run)
            step = app.step_manager.create(run, "tool", "act")
            app.step_manager.interrupt(step)
        return ToolCall("act", arguments, step.id, project.id, session.id, run.id, "work",
                        operation_token(project.id, session.id, "work"))

    async def app(self, *, responses=None, components=(), policy=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        model = ScriptedCompletion(*(responses or [[chunk("answer", finish="stop")]]))
        app = LargeLanguageModel(directory.name, components=components,
            engines={"custom": LoopEngine(completion_fn=model)}, services=ServiceConfig(tool_policy=policy or ToolPolicy()))
        self.addAsyncCleanup(app.shutdown)
        return app, model

    async def completed(self, app, count=1, components=()):
        project = await app.projects.acreate(config=ProjectConfig(parameters={"engines": {"custom": {'config': {'completion': {'model': 'test'}}}}}), components=components)
        session = await project.sessions.acreate()
        runs = []
        for _ in range(count):
            run = await (await session.run.submit("request", engine="custom")).wait()
            self.assertEqual(run.data.status, "completed", run.data.error)
            runs.append(run)
        await session.run.shutdown()
        return project, session, runs

    async def test_schema_is_dynamic_detached_and_supports_local_references(self):
        class Custom(Component):
            name = directory = "custom"
            capabilities = ()
            def configuration_schema(self):
                return implementation_schema(config={'type': 'object', '$defs': {'positive': {'type': 'integer', 'minimum': 1}}, 'properties': {'limit': {'$ref': '#/properties/config/$defs/positive'}}, 'additionalProperties': True})
        app, _ = await self.app(components=[Custom(), MemoryComponent(), RAGComponent()])
        schema = app.project_schema(components=["custom"])
        self.assertEqual(set(schema["properties"]["config"]["properties"]["parameters"]["properties"]["components"]["properties"]), {"custom", "memory", "rag"})
        validator = Draft202012Validator(schema)
        validator.validate({"config": {"parameters": {"components": {"custom": {'config': {'limit': 2, 'new': {'key': True}}}}}}})
        self.assertTrue(list(validator.iter_errors({"config": {"parameters": {"components": {"custom": {'config': {'limit': 0}}}}}})))
        schema["properties"].clear()
        self.assertIn("config", app.project_schema()["properties"])
        with self.assertRaises(ValueError):
            app.project_schema(components=["missing"])
        all_schema = app.project_schema()
        self.assertNotIn("default_engine", all_schema["properties"]["config"]["properties"])
        for name, spec in all_schema["properties"]["config"]["properties"]["parameters"]["properties"]["components"]["properties"].items():
            Draft202012Validator(spec).validate({})
        project = await app.projects.acreate(components=["memory"])
        value = await project.aconfiguration()
        self.assertEqual(value["schema"]["x-selected-components"], ["memory"])
        Draft202012Validator(value["schema"]).validate(value["values"])
        self.assertIn("memory", value["component_versions"])
        json.dumps(all_schema, allow_nan=False)

    async def test_engine_configuration_key_and_default_component_fields_are_discoverable(self):
        class Notes(Component):
            name = directory = "notes"
            def configuration_schema(self):
                return implementation_schema(config={'type': 'object', 'properties': {'language': {'type': 'string'}, 'format': {'type': 'object', 'properties': {'width': {'type': 'integer'}}}, 'optional': {}}})
        app, _ = await self.app(components=[Notes()])
        app.engines.register("other", LoopEngine(settings_name="shared"))
        schema = app.project_schema()
        self.assertEqual(schema["x-engines"]["other"]["configuration_key"], "shared")
        self.assertIn("shared", schema["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"])
        fields = schema["properties"]["config"]["properties"]["parameters"]["properties"]["components"]["properties"]["notes"]["properties"]
        self.assertEqual(fields["config"]["properties"]["language"]["type"], "string")
        self.assertEqual(fields["config"]["properties"]["format"]["properties"]["width"]["type"], "integer")
        self.assertNotIn("type", fields["config"]["properties"]["optional"])

    async def test_empty_registry_cannot_offer_unknown_component(self):
        app, _ = await self.app()
        validator = Draft202012Validator(app.project_schema())
        validator.validate({"components": []})
        self.assertTrue(list(validator.iter_errors({"components": ["unknown"]})))

    async def test_tool_contract_rejects_before_provider_and_effects(self):
        effects = []
        async def effect(arguments):
            effects.append(arguments)
        for contract in (ToolContract(approval_required=True), ToolContract(operation_key_required=True), ToolContract(isolation="sandbox")):
            app, model = await self.app(components=[RuntimeTools(ToolRegistry((Tool("act", "Act", {"type": "object"}, effect, contract=contract),)))])
            project = await app.projects.acreate(components=["tools"], config={"parameters": {"engines": {"custom": {'config': {'completion': {'model': 'test'}}}}}})
            await project.components.tools.aenable("act")
            session = await project.sessions.acreate()
            run = await (await session.run.submit("act", engine="custom")).wait()
            self.assertEqual(run.data.error_code, "tool_contract")
            self.assertEqual(model.requests, [])
        self.assertEqual(effects, [])

    async def test_external_probe_preserves_null_and_prevents_second_effect(self):
        async def probe(value):
            self.assertEqual(value["arguments"], {"id": 1})
            return {"status": "completed", "result": None, "evidence": "external receipt 7"}
        app, _ = await self.app(policy=ToolPolicy(operation_probe=probe))
        project = await app.projects.acreate()
        session = await project.sessions.acreate()
        tool_call = self.operation_call(app, project, session, {"id": 1})
        with app.project_manager.ownership.scope():
            app.run_repository.claim_tool_operation(session.data, tool_call)
        preview = await session.run.verify_operation("work")
        self.assertEqual(preview["observation"]["status"], "completed")
        self.assertEqual((await session.run.aoperation("work"))["status"], "started")
        result = await session.run.verify_operation("work", apply=True)
        self.assertIsNone(result["result"])
        self.assertEqual(len(result["observations"]), 1)
        with app.project_manager.ownership.scope():
            self.assertEqual(app.run_repository.claim_tool_operation(session.data, tool_call), {"reused": True, "result": None})

    async def test_operation_probe_detects_concurrent_change(self):
        async def probe(value):
            await session.run.areconcile_operation("work", result=7, evidence="manual result")
            return {"status": "not_applied", "evidence": "late answer"}
        app, _ = await self.app(policy=ToolPolicy(operation_probe=probe))
        project = await app.projects.acreate()
        session = await project.sessions.acreate()
        value = self.operation_call(app, project, session, {})
        with app.project_manager.ownership.scope():
            app.run_repository.claim_tool_operation(session.data, value)
        with self.assertRaisesRegex(ValueError, "changed"):
            await session.run.verify_operation("work", apply=True)
        self.assertEqual((await session.run.aoperation("work"))["result"], 7)

    async def test_recovery_repairs_output_gap_without_model_call(self):
        app, model = await self.app()
        project, session, runs = await self.completed(app)
        store = app.project_manager.sessions.conversations(session.data)
        with app.project_manager.ownership.scope():
            store.set_status(runs[0].data.assistant_message_id, MessageStatus.STREAMING)
            store.delta(runs[0].data.assistant_message_id, "wrong", operation="replace")
        plan = await project.arecovery()
        self.assertIn("output_gap", [i.code for i in plan.issues])
        applied = await project.arecovery(apply=True, expected_version=plan.version)
        self.assertEqual(applied.remaining.issues, [])
        self.assertEqual((await runs[0].aresponse()).content, "answer")
        self.assertEqual(len(model.requests), 1)
        again = await project.arecovery()
        self.assertEqual(again.repair_sessions, [])

    async def test_recovery_rejects_changed_plan_and_does_not_guess_missing_history(self):
        app, _ = await self.app()
        project, session, runs = await self.completed(app)
        plan = await project.arecovery()
        value = runs[0].data
        value.input_message_id = "missing"
        with app.project_manager.ownership.scope():
            app.run_repository.save(value)
        with self.assertRaisesRegex(ValueError, "recovery_conflict"):
            await project.arecovery(apply=True, expected_version=plan.version)
        damaged = await project.arecovery()
        self.assertIn("invalid_input_reference", [i.code for i in damaged.issues])
        self.assertEqual(damaged.repair_sessions, [])

    async def test_retention_counter_owns_explicit_parameters_without_engine_settings(self):
        app, _ = await self.app()
        project, _, _ = await self.completed(app)
        calls = []
        app.policy_resolver.token_counters["retention"] = lambda request: calls.append(request) or 7
        for unit in ("run", "session"):
            for params in ({}, {"model": "counter/model", "custom": 4}):
                with self.subTest(unit=unit, params=params):
                    config = (await project.aget_data()).config
                    config.policies["retention"] = {"unit": unit, "max_tokens": 1,
                        "counter": "retention", "counter_params": params}
                    await project.asave(config=config)
                    await project.aretention()
                    request = calls[-1]
                    self.assertTrue(request["messages"])
                    self.assertEqual({key: value for key, value in request.items() if key != "messages"}, params)
        with self.assertRaises(ValueError):
            ProjectConfig(policies={"retention": {"counter_params": {"messages": []}}})

    async def test_run_retention_keeps_session_latest_turn_and_cached_projection(self):
        app, _ = await self.app(responses=[[chunk(str(i), finish="stop")] for i in range(3)])
        project, session, runs = await self.completed(app, 3)
        store = app.project_manager.sessions.conversations(session.data)
        self.assertEqual(len(store.list()), 6)
        await project.aconfigure_policies({"retention": {"unit": "run", "keep_runs": 1, "max_bytes": 1}})
        plan = await project.aretention()
        self.assertEqual({r["run_id"] for r in plan.candidates}, {r.id for r in runs[:2]})
        await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual([r.id for r in await session.run.alist()], [runs[2].id])
        self.assertEqual(len(store.list()), 2)
        self.assertEqual(session.data.id, session.id)
        self.assertEqual((await project.arecovery()).issues, [])

    async def test_interrupted_retention_rolls_back_messages_and_runs(self):
        app, _ = await self.app(responses=[[chunk(str(i), finish="stop")] for i in range(3)])
        project, session, runs = await self.completed(app, 2)
        await project.aconfigure_policies({"retention": {"unit": "run", "keep_runs": 1, "max_bytes": 1}})
        plan = await project.aretention()
        with patch.object(app.run_repository, "delete_history", side_effect=OSError("disk interruption")):
            with self.assertRaises(OSError):
                await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual({r.id for r in await session.run.alist()}, {r.id for r in runs})
        self.assertEqual(len(await session.aconversation()), 4)
        self.assertEqual((await project.arecovery()).issues, [])
        self.assertEqual(await project.arecover_retention(), [])
        plan = await project.aretention()
        await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual([r.id for r in await session.run.alist()], [runs[1].id])
        run = await (await session.run.submit("new", engine="custom")).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)

    async def test_retention_protects_active_usage_and_component_references(self):
        class Referencing(Component):
            name = directory = "refs"
            def history_references(self, project):
                return {"run_ids": self.configuration(project).get("config", {}).get("runs", [])}
        app, _ = await self.app(responses=[[chunk(str(i), finish="stop")] for i in range(2)], components=[Referencing()])
        project, session, runs = await self.completed(app, 2, ["refs"])
        await project.components.refs.aconfigure({'config': {'runs': [runs[0].id]}})
        await project.aconfigure_policies({"retention": {"unit": "run", "keep_runs": 0, "max_bytes": 1}})
        plan = await project.aretention()
        self.assertEqual([r["run_id"] for r in plan.candidates], [runs[1].id])
        await project.aconfigure_policies({"usage": {"project_max_calls": 10}})
        self.assertEqual((await project.aretention()).candidates, [])

    async def test_component_maintenance_is_opt_in_and_preserves_failed_jobs(self):
        app, _ = await self.app(components=[RAGComponent()])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        data = project.components.rag
        first = await data.aenqueue_document(title="one", content="# One")
        second = await data.aenqueue_document(title="two", content="# Two")
        root = project.paths.root / "rag" / "jobs"
        for job, status in ((first, "completed"), (second, "failed")):
            value = read_json(root / job["id"] / "job.json")
            value.update(status=status, ended_at="2000-01-01T00:00:00+00:00")
            atomic_json(root / job["id"] / "job.json", value)
        self.assertEqual((await project.amaintenance())["components"]["rag"]["candidates"], [])
        await data.aconfigure({'policy': {'retention': {'job_max_age_seconds': 60}}})
        plan = await project.amaintenance()
        self.assertEqual([r["job_id"] for r in plan["components"]["rag"]["candidates"]], [first["id"]])
        await project.amaintenance(apply=True, expected_version=plan["version"])
        self.assertFalse((root / first["id"]).exists())
        self.assertTrue((root / second["id"]).exists())

    def rag(self, *, embed=None):
        async def embedding(**request):
            return {"data": [{"index": i, "embedding": [1.0, 0.5]} for i, _ in enumerate(request["input"])],
                    "usage": {"total_tokens": 3}}
        async def extraction(**request):
            return {"choices": [{"message": {"content": '{"entities": [], "relations": []}'}}],
                    "usage": {"total_tokens": 7}}
        return RAGComponent(embedding=EmbeddingModel(model="test/embedding", embedding_fn=embed or embedding),
                            extractor=TripleExtractor(model="test/extract", completion_fn=extraction, max_tokens=20))

    async def test_independent_rag_and_loop_share_project_call_quota(self):
        app, model = await self.app(components=[self.rag()])
        project = await app.projects.acreate(components=["rag"], config=rag_project({"parameters": {"engines": {"custom": {'config': {'completion': {'model': 'test'}}}}}}))
        await project.aconfigure_policies({"usage": {"project_max_calls": 2}})
        await project.components.rag.aadd_document(title="Guide", content="# Guide\nLocal scripting reference.")
        receipts = await project.components.rag.amodel_usage()
        self.assertEqual({r["operation"] for r in receipts}, {"aembedding", "acompletion"})
        self.assertTrue(all(r["usage_complete"] for r in receipts))
        session = await project.sessions.acreate()
        run = await (await session.run.submit("answer", engine="custom")).wait()
        self.assertEqual(run.data.error_code, "usage_limit")
        self.assertEqual(model.requests, [])
        usage = await project.amodel_usage()
        self.assertEqual((usage["call_count"], usage["known_tokens"]), (2, 10))
        await session.run.shutdown()
        with self.assertRaisesRegex(ValueError, "quota"):
            await project.components.aremove("rag", permanent=True)

    async def test_rag_tool_observations_count_toward_owning_run(self):
        app, model = await self.app(components=[self.rag()], responses=[
            [chunk(calls=[call('{"query":"reference"}', name="rag_search")], finish="tool_calls")],
            [chunk("should not call", finish="stop")]])
        project = await app.projects.acreate(components=["rag"], config=rag_project({"parameters": {"engines": {"custom": {'config': {'completion': {'model': 'test'}}}}}}))
        await project.components.rag.aadd_document(title="Guide", content="# Guide\nLocal scripting reference.")
        await project.aconfigure_policies({"usage": {"max_calls": 2}})
        session = await project.sessions.acreate()
        run = await (await session.run.submit("search", engine="custom")).wait()
        self.assertEqual(run.data.error_code, "usage_limit", run.data.error)
        self.assertEqual(len(model.requests), 1)
        owned = [r for r in await project.components.rag.amodel_usage() if r.get("run_id") == run.id]
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]["operation"], "aembedding")

    async def test_parallel_component_calls_reserve_before_effect_and_cancel_is_accounted(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def embedding(**request):
            calls.append(1)
            entered.set()
            await release.wait()
            return {"data": [{"index": 0, "embedding": [1.0, 0.5]}]}
        app, _ = await self.app(components=[self.rag(embed=embedding)])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        await project.aconfigure_policies({"usage": {"project_max_calls": 1}})
        data = project.components.rag
        first = asyncio.create_task(data.aadd_document(title="one", content="# First"))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with self.assertRaisesRegex(ValueError, "unfinished"):
                await project.components.aremove("rag", permanent=True)
            with self.assertRaisesRegex(Exception, "exhausted"):
                await data.aadd_document(title="two", content="# Second")
        finally:
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
        self.assertEqual(calls, [1])
        usage = await project.amodel_usage()
        self.assertEqual((usage["call_count"], usage["unknown_calls"]), (1, 1))
        self.assertEqual((await data.amodel_usage())[0]["status"], "failed")

    async def test_unobservable_rag_client_cannot_bypass_quota(self):
        class Client:
            async def embed(self, *args, **kwargs):
                raise AssertionError("must not call")
        app, _ = await self.app(components=[RAGComponent(embedding=Client(), embedding_id="fake", extractor=Client())])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        await project.aconfigure_policies({"usage": {"project_max_calls": 1}})
        with self.assertRaisesRegex(Exception, "observable"):
            await project.components.rag.aadd_document(title="Doc", content="# Text")

    async def test_corrupt_usage_receipt_blocks_new_calls(self):
        from llm.providers.requests import ProviderError
        async def embedding(**request):
            raise ConnectionError("response lost")
        app, _ = await self.app(components=[self.rag(embed=embedding)])
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        await project.components.rag.aconfigure(rag_settings({'policy': {'provider': {'max_attempts': 1}}}))
        data = project.components.rag
        with self.assertRaises(ProviderError):
            await data.aadd_document(title="Doc", content="# Text")
        receipt = (await data.amodel_usage())[0]
        receipt.update(usage_complete=True, usage={"total_tokens": -100})
        atomic_json(project.paths.root / "rag" / "usage" / (receipt["id"] + ".json"), receipt)
        with self.assertRaisesRegex(ValueError, "Invalid complete"):
            await data.aadd_document(title="Another", content="# Text")

    async def test_retention_finishes_committed_directory_cleanup(self):
        import shutil
        app, _ = await self.app(responses=[[chunk(str(i), finish="stop")] for i in range(2)])
        project, session, runs = await self.completed(app, 2)
        await project.aconfigure_policies({"retention": {"unit": "run", "keep_runs": 1, "max_bytes": 1}})
        plan = await project.aretention()
        original = shutil.rmtree
        def interrupted(path, *args, **kwargs):
            if Path(path).name.startswith("gc-"):
                for record in Path(path).glob("*.removed/run.json"):
                    record.unlink()
                raise OSError("power failure during directory deletion")
            return original(path, *args, **kwargs)
        with patch("llm.services.infrastructure.storage.shutil.rmtree", interrupted):
            with self.assertWarnsRegex(RuntimeWarning, "cleanup"):
                await project.aretention(apply=True, expected_version=plan.version)
        self.assertEqual(await project.arecover_retention(), [])
        self.assertFalse(runs[0].session.paths.runs.joinpath(runs[0].id).exists())
        self.assertEqual([r.id for r in await session.run.alist()], [runs[1].id])

    async def test_component_token_reservation_is_retained_when_usage_is_missing(self):
        from llm.providers.requests import ProviderError
        async def embedding(**request):
            raise ConnectionError("response lost")
        app, _ = await self.app(components=[self.rag(embed=embedding)])
        app.project_manager.usage_counters["fixed"] = lambda request: 6
        project = await app.projects.acreate(components=["rag"], config=rag_project())
        await project.components.rag.aconfigure(rag_settings({'policy': {'provider': {'max_attempts': 1}}}))
        await project.aconfigure_policies({"usage": {"project_max_tokens": 10, "counter": "fixed"}})
        data = project.components.rag
        with self.assertRaises(ProviderError):
            await data.aadd_document(title="Doc", content="# Text")
        self.assertEqual((await project.amodel_usage())["reserved_tokens"], 6)
        with self.assertRaisesRegex(Exception, "exhausted"):
            await data.aadd_document(title="Another", content="# Text")
        self.assertEqual((await project.amodel_usage())["call_count"], 1)

    async def test_changed_tool_policy_revision_rejects_old_approval(self):
        from llm.services.runtime.tools import ToolApprovalRequired
        from dataclasses import replace
        async def approve(call):
            raise ToolApprovalRequired("review")
        async def effect(arguments):
            self.fail("old approval must not execute")
        app, _ = await self.app(components=[RuntimeTools(ToolRegistry((
            Tool("act", "Act", {"type": "object"}, effect, contract=ToolContract(approval_required=True)),)))],
            responses=[[chunk(calls=[call('{}', name="act")], finish="tool_calls")]], policy=ToolPolicy(authorize=approve))
        project = await app.projects.acreate(components=["tools"], config={"parameters": {"engines": {"custom": {'config': {'completion': {'model': 'test'}}}}}})
        await project.components.tools.aenable("act")
        session = await project.sessions.acreate()
        run = await (await session.run.submit("act", engine="custom")).wait()
        self.assertEqual(run.data.status, "paused")
        checkpoint = await run.acheckpoint("loop")
        decisions = {k: True for k, v in checkpoint["records"].items() if v["status"] == "waiting"}
        await session.run.shutdown()
        app.services = replace(app.services, tool_policy=ToolPolicy(authorize=approve, revision="2"))
        with self.assertRaisesRegex(Exception, "changed"):
            await session.run.resume(run.id, engine="custom", decisions=decisions)
