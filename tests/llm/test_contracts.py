"""공개 데이터 계약의 실제 서비스 연결, 직렬화, 소유권과 실행 정책 분리를 검증한다."""

import asyncio
import json
import unittest
from dataclasses import replace

from llm.llm import (Diagnostic, ResourceRef, OperationProgress, SessionRuntimeView, RunView,
    ResumePlan, RecoveryPlan, RecoveryResult, RetentionPlan, EngineEvent, EngineEventType)
from llm.core.interactions import approval_request
from llm.services.runtime.policies import ExecutionLimitError
from llm.services.runtime.tools import ToolExecutionError
from tests.llm import test_long_running, test_interactions, test_rag_components
from tests.llm.test_loop import chunk, call


class ContractTests(unittest.TestCase):
    def test_diagnostic_roundtrip_copies_open_details_without_execution_authority(self):
        details = {"provider": {"arbitrary": [1, 2]}}
        diagnostic = Diagnostic("busy", "wait", "warning", ResourceRef("model", "custom/model"), details)
        details["provider"]["arbitrary"].append(3)
        value = json.loads(json.dumps(diagnostic.to_dict()))
        self.assertEqual(Diagnostic.from_dict(value), diagnostic)
        value["details"]["provider"]["arbitrary"].append(4)
        self.assertEqual(diagnostic.details["provider"]["arbitrary"], [1, 2])
        self.assertFalse(hasattr(diagnostic, "retry"))
        self.assertEqual(ExecutionLimitError("tool_denied", "denied").diagnostic.code, "tool_denied")
        failure = ToolExecutionError("unknown", retryable=True)
        self.assertEqual(failure.diagnostic.details, {"effect": "uncertain", "retryable": True})
        self.assertEqual(failure.effect, "uncertain")
        failure.code = None
        self.assertEqual(Diagnostic.from_exception(failure).code, "execution_failed")

    def test_common_observations_do_not_restrict_custom_metadata(self):
        metadata = {"phase": 3, "error_code": 503, "custom": {"provider": "future"}}
        event = EngineEvent(EngineEventType.STEP_FAILED, step_id="step", metadata=metadata, error="busy")
        self.assertEqual(event.progress.phase, "failed")
        self.assertEqual(event.diagnostic.code, "step_failed")
        self.assertEqual(event.metadata, metadata)

    def test_contracts_reject_invalid_json_types_and_unknown_fields(self):
        for value in ({"code": "x", "message": "x", "severity": "critical"},
                      {"code": 1, "message": "x"}, {"code": "x", "message": "x", "retry": True}):
            with self.assertRaises((TypeError, ValueError)):
                Diagnostic.from_dict(value)
        source = ResourceRef("job", "one")
        for values in ({"completed": True}, {"completed": -1}, {"completed": float("inf")},
                       {"completed": 2, "total": 1}, {"total": 5}):
            with self.assertRaises(ValueError):
                OperationProgress(source, "indexing", **values)
        unknown = OperationProgress(source, "indexing", completed=2, unit="batches")
        self.assertIsNone(OperationProgress.from_dict(unknown.to_dict()).total)


class RuntimeContractTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_long_running.LongRunningTests.setup_app
    paused = test_interactions.InteractionRuntimeTests.paused

    async def test_run_session_step_views_roundtrip_and_are_detached(self):
        async def act(arguments):
            return None
        app, session, _ = await self.setup_app(act, [[chunk('answer', finish='stop')]])
        run = await (await session.run.submit("go", engine="loop")).wait()
        state = await session.run.astatus()
        self.assertIsInstance(state, SessionRuntimeView)
        self.assertEqual(SessionRuntimeView.from_dict(json.loads(json.dumps(state.to_dict()))), state)
        view = await run.aview()
        restored = RunView.from_dict(json.loads(json.dumps(view.to_dict())))
        self.assertEqual(restored, view)
        restored.run.metadata["ui_only"] = True
        self.assertNotIn("ui_only", run.data.metadata)
        self.assertTrue(all(step.progress.phase == step.status.value for step in await run.steps.alist()))
        self.assertEqual(view.outputs, (await run.aview()).outputs)

    async def test_resume_plan_is_typed_and_does_not_admit_a_request(self):
        app, session, run = await self.paused()
        plan = await session.run.resume_plan(run.id, engine="loop")
        self.assertIsInstance(plan, ResumePlan)
        self.assertIsInstance(plan.blockers[0], Diagnostic)
        self.assertEqual(plan.source.id, run.id)
        self.assertEqual(ResumePlan.from_dict(json.loads(json.dumps(plan.to_dict()))), plan)
        self.assertEqual((await session.run.astatus()).queued_count, 0)
        await run.arespond(plan.interactions[0].request.respond("approve"))
        self.assertTrue((await session.run.resume_plan(run.id, engine="loop")).can_resume)
        self.assertEqual(self.effects, [])
        resumed = await (await session.run.resume(run.id, engine="loop")).wait()
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)

    async def test_recovery_and_retention_keep_review_versions_and_json_journal(self):
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('done', finish='stop')]])
        await (await session.run.submit("go", engine="loop")).wait()
        await session.run.shutdown()
        recovery = await session.project.arecovery()
        self.assertIsInstance(recovery, RecoveryPlan)
        self.assertEqual(RecoveryPlan.from_dict(recovery.to_dict()), recovery)
        applied = await session.project.arecovery(apply=True, expected_version=recovery.version)
        self.assertIsInstance(applied, RecoveryResult)
        self.assertEqual(RecoveryResult.from_dict(json.loads(json.dumps(applied.to_dict()))), applied)
        retention = await session.project.aretention()
        self.assertIsInstance(retention, RetentionPlan)
        self.assertEqual(RetentionPlan.from_dict(retention.to_dict()), retention)
        await session.project.aconfigure_policies({"retention": {"unit": "session", "max_bytes": 1}})
        with self.assertRaisesRegex(ValueError, "conflict"):
            await session.project.aretention(apply=True, expected_version=retention.version)
        self.assertEqual(len(await session.project.sessions.alist()), 1)

    async def test_failure_diagnostic_and_interrupted_progress_survive_reload(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def act(arguments):
            entered.set()
            await release.wait()
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        request = await session.run.submit("work", engine="loop")
        await asyncio.wait_for(entered.wait(), 5)
        await session.run.interrupt()
        run = await request.wait()
        self.assertEqual((await run.aresult()).diagnostic.source.run_id, run.id)
        steps = await run.steps.alist()
        self.assertTrue(any(step.status == "interrupted" for step in steps))
        for step in steps:
            self.assertEqual(step.progress.phase, step.status.value)
            self.assertEqual(step.progress.source.run_id, run.id)

    async def test_tool_diagnostic_preserves_effect_without_authorizing_retry(self):
        effects = []
        async def act(arguments):
            effects.append(1)
            raise ToolExecutionError("effect uncertain", effect="uncertain", retryable=True)
        app, session, _ = await self.setup_app(act, [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]])
        run = await (await session.run.submit("work", engine="loop")).wait()
        step = next(s for s in await run.steps.alist() if s.kind == "tool")
        self.assertEqual(step.diagnostic.details["effect"], "uncertain")
        self.assertEqual(step.diagnostic.source.run_id, run.id)
        self.assertEqual(step.progress.phase, "failed")
        self.assertEqual(effects, [1])

    async def test_custom_progress_is_persisted_and_typed_after_reopen(self):
        from llm.engines.base import BaseEngine
        from llm.core.models import new_id
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [])
        class Counted:
            async def execute(self, context):
                step_id = new_id()
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id)
                yield EngineEvent(EngineEventType.STEP_UPDATED, step_id=step_id,
                    progress=OperationProgress(ResourceRef("step", step_id), "indexing", 2, 5, "documents"))
                yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id,
                    progress=OperationProgress(ResourceRef("step", step_id), "completed", 5, 5, "documents"))
        app.engines.register("counted", Counted())
        run = await (await session.run.submit("index", engine="counted")).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        step = (await run.steps.alist())[0]
        restored = await run.steps.aload(step.id)
        self.assertEqual((restored.progress.completed, restored.progress.total, restored.progress.unit), (5, 5, "documents"))
        self.assertEqual(restored.progress.source.run_id, run.id)

    async def test_foreign_step_observation_rejected_before_effect(self):
        from llm.services.lifecycle.steps import StepEventRecorder
        async def act(arguments):
            pass
        app, session, _ = await self.setup_app(act, [[chunk('done', finish='stop')]])
        run = await (await session.run.submit("go", engine="loop")).wait()
        from llm.core.models import new_id
        step_id = new_id()
        event = EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id,
            progress=OperationProgress(ResourceRef("step", "foreign", step_id="foreign"), "starting"))
        recorder = StepEventRecorder(app.step_manager)
        with self.assertRaisesRegex(ValueError, "ownership"):
            recorder.record(run.data, event)
        self.assertFalse(app.step_manager.repository.exists(run.data, step_id))


class RAGProgressTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_rag_components.RAGTests.asyncSetUp

    async def test_job_progress_uses_same_contract_without_creating_a_run(self):
        job = await self.rag.aenqueue_document(title="manual", content="local reference")
        progress = await self.rag.ajob_progress(job["id"])
        self.assertIsInstance(progress, OperationProgress)
        self.assertEqual((progress.source.component, progress.phase), ("rag", "queued"))
        self.assertIsNone(progress.total)
        await self.rag.acancel_job(job["id"])
        self.assertEqual((await self.rag.ajob_progress(job["id"])).phase, "cancelled")
        self.assertEqual(await self.project.sessions.alist(), [])
