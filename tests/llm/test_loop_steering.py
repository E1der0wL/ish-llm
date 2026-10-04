"""실제 Facade/저장/Loop 경계에서 추가 지시와 기존 실행 계약을 검증한다."""

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig, EngineEventType, ServiceConfig
from llm.core.steering import InstructionStatus
from llm.services.runtime.runs import RunRequestError
from tests.llm.test_loop import chunk, call
from tests.llm.support.runtime_tools import RuntimeTools
from llm.components.tools import Tool, ToolRegistry


class Model:
    def __init__(self, *responses, gate=True):
        self.responses = list(responses)
        self.requests = []
        self.entered, self.release = threading.Event(), threading.Event()
        if not gate:
            self.release.set()

    def __call__(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        if len(self.requests) == 1:
            self.entered.set()
            if not self.release.wait(10):
                raise TimeoutError("Test model gate was not released")
        for value in self.responses.pop(0):
            if isinstance(value, Exception):
                raise value
            yield value


class LoopSteeringTests(unittest.IsolatedAsyncioTestCase):
    async def setup_backend(self, model, *, options=None, config=None, components=None, on_event=None, services=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = LargeLanguageModel(Path(folder.name), components=components or [], services=services,
            engines={"worker": LoopEngine(completion_fn=model, **(options or {}))}, on_event=on_event)
        self.addAsyncCleanup(app.shutdown)
        self.addCleanup(model.release.set)
        project = await app.projects.acreate("test", config=ProjectConfig(ProjectConfig.merge(
            {"parameters": {"engines": {"worker": {"completion": {"model": "test/model"}}}}}, config or {})),
            components=[v.name for v in (components or [])])
        for component in components or []:
            if isinstance(component, RuntimeTools):
                await (await project.components.aget("tools")).aconfigure({"enabled": list(component.registry.names())})
        session = await project.sessions.acreate("test")
        request = await session.run.submit("original", engine="worker")
        self.assertTrue(await asyncio.to_thread(model.entered.wait, 10))
        run = await request.aget_run()
        return app, session, request, run

    async def test_same_run_multiple_instructions_and_separate_queued_request(self):
        events = []
        model = Model([chunk("draft", finish="stop")], [chunk("revised", finish="stop")],
                      [chunk("next", finish="stop")])
        app, session, request, run = await self.setup_backend(model, on_event=lambda r, e: events.append(e))
        a = await session.run.steer(run.id, "first correction")
        b = await session.run.steer(run.id, "second correction")
        following = await session.run.submit("ordinary request", engine="worker")
        self.assertEqual((await session.run.astatus()).queued_count, 1)
        self.assertEqual((await run.aget_data()).status, "running")
        self.assertEqual(a.status, InstructionStatus.PENDING)
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.id, run.id)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual((await result.aresponse()).content, "revised")
        await following.wait(timeout=10)
        self.assertEqual(len(await session.run.alist()), 2)
        self.assertEqual([m["content"] for m in model.requests[1]["messages"]],
                         ["original", "draft", "first correction", "second correction"])
        values = await run.ainstructions()
        self.assertEqual([v.id for v in values], [a.id, b.id])
        self.assertTrue(all(v.status == "applied" and v.applications[0]["iteration"] == 2 for v in values))
        checkpoint = await run.acheckpoint("loop")
        self.assertEqual(checkpoint["records"]["steering:2"]["message_ids"], [a.id, b.id])
        self.assertTrue(any(e.type == EngineEventType.STEERING_CHANGED for e in events))
        with self.assertRaises(RunRequestError):
            await session.run.steer(run.id, "too late")

    async def test_tool_exchange_completes_before_instruction_is_included(self):
        entered, release = asyncio.Event(), asyncio.Event()
        effects = []
        async def act(arguments):
            entered.set()
            await release.wait()
            effects.append(1)
        component = RuntimeTools(ToolRegistry([Tool("act", "test", {"type": "object"}, act)]))
        model = Model([chunk(calls=[call("{}", name="act")], finish="tool_calls")],
                      [chunk("done", finish="stop")], gate=False)
        app, session, request, run = await self.setup_backend(model, components=[component])
        await asyncio.wait_for(entered.wait(), 10)
        await session.run.steer(run.id, "new instruction")
        self.assertEqual(effects, [])
        release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual([m["role"] for m in model.requests[1]["messages"]], ["user", "assistant", "tool", "user"])

    async def test_resume_after_reopen_reconstructs_instruction_without_repeating_completion(self):
        model = Model([chunk("draft", finish="stop")], [ConnectionError("offline")],
                      [chunk("recovered", finish="stop")], [chunk("next answer", finish="stop")])
        app, session, request, run = await self.setup_backend(model,
            config={"policies": {"context": {"mode": "recent", "max_turns": 1}}})
        instruction = await session.run.steer(run.id, "correction")
        model.release.set()
        failed = await request.wait(timeout=10)
        self.assertEqual(failed.data.status, "failed")
        root = session.project.paths.root.parent.parent
        project_id, session_id = session.project.id, session.id
        await app.shutdown()
        reopened = LargeLanguageModel(root, components=[], engines={"worker": LoopEngine(completion_fn=model)})
        self.addAsyncCleanup(reopened.shutdown)
        restored = await (await reopened.projects.aload(project_id)).sessions.aload(session_id)
        resumed = await (await restored.run.resume(run.id, engine="worker")).wait(timeout=10)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(len(model.requests), 3)
        self.assertEqual([m["content"] for m in model.requests[2]["messages"]].count("correction"), 1)
        old = await restored.run.aload(run.id)
        value = (await old.ainstructions())[0]
        self.assertEqual(value.id, instruction.id)
        self.assertEqual([a["run_id"] for a in value.applications], [run.id, resumed.id])
        following = await (await restored.run.submit("next", engine="worker")).wait(timeout=10)
        self.assertEqual(following.data.status, "completed", following.data.error)
        self.assertEqual([m["content"] for m in model.requests[-1]["messages"]],
                         ["original", "correction", "recovered", "next"])

    async def test_iteration_limit_does_not_disappear_when_steered(self):
        model = Model([chunk("answer", finish="stop")])
        _, session, request, run = await self.setup_backend(model, options={"max_iterations": 1})
        await session.run.steer(run.id, "correction")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await run.ainstructions())[0].status, "unapplied")
        self.assertEqual((await run.ainstructions())[0].reason, "iteration_limit")

    async def test_current_turn_cannot_be_trimmed_to_only_latest_instruction(self):
        model = Model([chunk("x" * 50, finish="stop")])
        services = ServiceConfig(token_counters={"chars": lambda request: sum(len(m.get("content") or "") for m in request["messages"])})
        _, session, request, run = await self.setup_backend(model, services=services,
            config={"policies": {"completion": {"max_tokens": 20, "counter": "chars"}}})
        await session.run.steer(run.id, "new")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.error_code, "context_budget_exceeded", result.data.error)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await run.ainstructions())[0].status, "unapplied")

    async def test_checkpoint_write_failure_prevents_new_provider_call(self):
        model = Model([chunk("draft", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        await session.run.steer(run.id, "correction")
        original = app.run_repository.record_checkpoint
        def fail(record, event):
            if event.metadata.get("key") == "steering:2":
                raise OSError("disk full")
            return original(record, event)
        with patch.object(app.run_repository, "record_checkpoint", side_effect=fail):
            model.release.set()
            result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "failed")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await run.ainstructions())[0].status, "unapplied")

    async def test_final_output_closes_admission_even_before_run_status_changes(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def observe(run, event):
            if event.type == EngineEventType.OUTPUT and event.output.step_id is None:
                entered.set()
                await release.wait()
        model = Model([chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model, on_event=observe)
        model.release.set()
        await asyncio.wait_for(entered.wait(), 10)
        try:
            self.assertEqual((await run.aget_data()).status, "running")
            with self.assertRaises(RunRequestError):
                await session.run.steer(run.id, "too late")
        finally:
            release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")

    async def test_memory_store_uses_same_delivery_contract(self):
        model = Model([chunk("draft", finish="stop")], [chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model, services=ServiceConfig(conversations="memory"))
        await session.run.steer(run.id, "correction")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual((await run.ainstructions())[0].status, "applied")

    async def test_interrupt_preserves_ordinary_queue_without_replaying_pending_instruction(self):
        entered = asyncio.Event()
        async def act(arguments):
            entered.set()
            await asyncio.Event().wait()
        component = RuntimeTools(ToolRegistry([Tool("act", "test", {"type": "object"}, act)]))
        model = Model([chunk(calls=[call("{}", name="act")], finish="tool_calls")],
                      [chunk("next", finish="stop")], gate=False)
        _, session, request, run = await self.setup_backend(model, components=[component])
        await asyncio.wait_for(entered.wait(), 10)
        await session.run.steer(run.id, "do not lose me")
        queued = await session.run.submit("next request", engine="worker")
        self.assertTrue(await session.run.interrupt())
        self.assertEqual((await request.wait(timeout=10)).data.status, "interrupted")
        self.assertEqual((await queued.wait(timeout=10)).data.status, "completed")
        value = (await run.ainstructions())[0]
        self.assertEqual((value.status, value.reason), ("unapplied", "interrupted"))
        self.assertFalse(any(m.get("content") == "do not lose me" for m in model.requests[1]["messages"]))
        self.assertEqual(len(await session.run.alist()), 2)

    async def test_unsupported_engine_rejects_instruction_before_persistence(self):
        from llm.engines.base import BaseEngine
        from llm.core.results import EngineOutput
        entered, release = asyncio.Event(), asyncio.Event()
        async def action(context):
            entered.set()
            await release.wait()
            return EngineOutput(text="done")
        model = Model([chunk("done", finish="stop")])
        app, session, first, _ = await self.setup_backend(model)
        model.release.set()
        await first.wait(timeout=10)
        app.engines.register("custom", BaseEngine(action=action))
        request = await session.run.submit("custom request", engine="custom")
        await asyncio.wait_for(entered.wait(), 10)
        run = await request.aget_run()
        try:
            with self.assertRaises(RunRequestError) as error:
                await session.run.steer(run.id, "unsupported")
            self.assertEqual(error.exception.code, "steering_unavailable")
            self.assertEqual(await run.ainstructions(), [])
        finally:
            release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")

    async def test_instruction_storage_failure_rolls_back_and_does_not_fail_run(self):
        model = Model([chunk("done", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        store = app.project_manager.sessions.conversations(session.data)
        create = store.create
        def fail(*args, **kwargs):
            create(*args, **kwargs)
            raise OSError("disk full after append")
        with patch.object(store, "create", side_effect=fail):
            with self.assertRaises(OSError):
                await session.run.steer(run.id, "not accepted")
        self.assertEqual(await run.ainstructions(), [])
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")

    async def test_apply_failure_rolls_back_receipt_and_never_calls_provider(self):
        model = Model([chunk("draft", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        await session.run.steer(run.id, "correction")
        from llm.services.runtime.steering import apply_instructions
        def fail(*args, **kwargs):
            apply_instructions(*args, **kwargs)
            raise OSError("receipt failed")
        with patch("llm.services.runtime.steering.apply_instructions", side_effect=fail):
            model.release.set()
            result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "failed")
        self.assertEqual(len(model.requests), 1)
        value = (await run.ainstructions())[0]
        self.assertEqual(value.applications, [])
        self.assertEqual(value.status, "unapplied")

    async def test_output_batching_keeps_instruction_checkpoint_barrier(self):
        from llm.services.runtime.output import OutputPolicy
        model = Model([chunk("draft"), chunk(finish="stop")], [chunk("revised", finish="stop")])
        app, session, request, run = await self.setup_backend(model,
            services=ServiceConfig(output_policy=OutputPolicy(batch_size=8)))
        value = await session.run.steer(run.id, "correction")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        stored = (await run.ainstructions())[0]
        self.assertEqual(stored.id, value.id)
        self.assertIsNotNone(stored.applications[0]["step_id"])

    async def test_processor_cannot_remove_live_instruction(self):
        from llm.components.processing import CompletionSession
        from tests.llm.test_completion_processing import Feature, Processor
        class Remove(CompletionSession):
            async def prepare(self, request):
                if request.iteration == 2:
                    request.messages.pop()
                if False:
                    yield
        model = Model([chunk("draft", finish="stop")])
        _, session, request, run = await self.setup_backend(model,
            components=[Feature("processor", Processor("remove", lambda _: Remove()))])
        await session.run.steer(run.id, "must survive")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "failed")
        self.assertIn("transcript structure", result.data.error)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await run.ainstructions())[0].status, "unapplied")

    async def test_recent_context_keeps_original_request_and_instruction_as_one_turn(self):
        model = Model([chunk("draft", finish="stop")], [chunk("answer", finish="stop")],
                      [chunk("next", finish="stop")])
        _, session, request, run = await self.setup_backend(model,
            config={"policies": {"context": {"mode": "recent", "max_turns": 1}}})
        await session.run.steer(run.id, "correction")
        model.release.set()
        await request.wait(timeout=10)
        following = await (await session.run.submit("next", engine="worker")).wait(timeout=10)
        self.assertEqual(following.data.status, "completed", following.data.error)
        self.assertEqual([m["content"] for m in model.requests[2]["messages"]],
                         ["original", "correction", "answer", "next"])

    async def test_queue_limit_counts_requests_not_instructions(self):
        model = Model([chunk("draft", finish="stop")], [chunk("answer", finish="stop")],
                      [chunk("next", finish="stop")])
        _, session, request, run = await self.setup_backend(model, config={"policies": {"run": {"max_queued": 1}}})
        await session.run.steer(run.id, "correction")
        following = await session.run.submit("next", engine="worker")
        with self.assertRaises(RunRequestError):
            await session.run.submit("too many", engine="worker")
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")
        self.assertEqual((await following.wait(timeout=10)).data.status, "completed")

    async def test_stale_recovery_does_not_schedule_instruction_as_a_new_run(self):
        from llm.core.models import RunStatus, MessageRole, MessageStatus
        model = Model([chunk("done", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        model.release.set()
        await request.wait(timeout=10)
        await session.run.shutdown()
        def make_stale():
            record = app.run_repository.load(session.data, run.id)
            record.status, record.ended_at = RunStatus.RUNNING, None
            record.metadata["steering"]["accepting"] = True
            app.run_repository.save(record)
            store = app.project_manager.sessions.conversations(session.data)
            store.set_status(record.assistant_message_id, MessageStatus.STREAMING)
            return store.create(MessageRole.USER, "pending before crash", MessageStatus.QUEUED, run_id=run.id,
                metadata={"steering": {"run_id": run.id, "input_message_id": record.input_message_id, "status": "pending",
                    "targets": [{"id": "root", "scope": None, "status": "pending", "applications": []}]}})
        message = await app._storage_call(make_stale)
        await session.run.start()
        await asyncio.wait_for(session.run.wait_idle(), 10)
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertEqual((await run.aget_data()).status, "interrupted")
        value = (await run.ainstructions())[0]
        self.assertEqual((value.id, value.status, value.reason), (message.id, "unapplied", "process_restart"))
        self.assertEqual(len(model.requests), 1)

    async def test_invalid_stored_instruction_blocks_recovery_before_any_state_change(self):
        from llm.core.models import RunStatus, MessageRole, MessageStatus
        from llm.core.steering import InstructionDataError
        from llm.services.lifecycle.steps import StepManager
        model = Model([chunk("done", finish="stop")], [chunk("new session", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        model.release.set()
        await request.wait(timeout=10)
        await session.run.shutdown()

        def make_stale():
            saved = app.run_repository.load(session.data, run.id)
            saved.status, saved.ended_at = RunStatus.RUNNING, None
            app.run_repository.save(saved)
            store = app.project_manager.sessions.conversations(session.data)
            store.set_status(saved.assistant_message_id, MessageStatus.STREAMING)
            store.create(MessageRole.USER, "old pending instruction", MessageStatus.QUEUED, run_id=run.id,
                metadata={"steering": {"run_id": run.id, "input_message_id": saved.input_message_id, "status": "pending"}})
            store.create(MessageRole.USER, "ordinary queued request", MessageStatus.QUEUED, metadata={"engine": "worker"})
            return store.list()

        before = await app._storage_call(make_stale)
        with self.assertRaises(InstructionDataError):
            await run.ainstructions()
        with patch.object(StepManager, "recover") as steps, patch.object(app.run_repository, "save") as save:
            with self.assertRaisesRegex(InstructionDataError, "targets"):
                await session.run.start()
            steps.assert_not_called()
            save.assert_not_called()
        self.assertEqual(await session.aconversation(), before)
        self.assertEqual((await run.aget_data()).status, "running")
        plan = await session.project.arecovery()
        self.assertIn("invalid_instruction_data", [issue.code for issue in plan.issues])
        self.assertEqual(plan.repair_sessions, [])
        self.assertEqual(len(model.requests), 1)
        # 원본 Session/기록은 보존되고 다른 Session의 정상 실행은 막지 않는다.
        other = await session.project.sessions.acreate("new")
        result = await (await other.run.submit("fresh", engine="worker")).wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)

    async def test_memory_restart_allows_new_request_after_instruction_loss(self):
        model = Model([chunk("draft", finish="stop")], [ConnectionError("offline")],
                      [chunk("fresh answer", finish="stop")])
        app, session, request, run = await self.setup_backend(model,
            services=ServiceConfig(conversations="memory"))
        await session.run.steer(run.id, "old instruction")
        model.release.set()
        failed = await request.wait(timeout=10)
        self.assertEqual(failed.data.status, "failed")
        root, project_id, session_id = session.project.paths.root.parents[1], session.project.id, session.id
        await app.shutdown()
        async with LargeLanguageModel(root, components=[], engines={"worker": LoopEngine(completion_fn=model)}) as reopened:
            restored = await (await reopened.projects.aload(project_id)).sessions.aload(session_id)
            self.assertEqual(await restored.aconversation(), [])
            completed = await (await restored.run.submit("fresh", engine="worker")).wait(timeout=10)
            self.assertEqual(completed.data.status, "completed", completed.data.error)
            self.assertEqual([m["content"] for m in model.requests[-1]["messages"]], ["fresh"])

    async def test_different_session_cannot_receive_target_run_instruction(self):
        model = Model([chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model)
        other = await session.project.sessions.acreate("other")
        with self.assertRaises((FileNotFoundError, ValueError)):
            await other.run.steer(run.id, "wrong session")
        self.assertEqual(await run.ainstructions(), [])
        model.release.set()
        await request.wait(timeout=10)

    async def test_instruction_is_not_a_request_handle(self):
        from llm.services.api import RequestHandle
        model = Model([chunk("draft", finish="stop")], [chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model)
        value = await session.run.steer(run.id, "correction")
        fake_request = RequestHandle(session, value.id)
        with self.assertRaises(ValueError):
            await fake_request.cancel()
        with self.assertRaises(ValueError):
            await fake_request.aget_run()
        model.release.set()
        await request.wait(timeout=10)

    async def test_resume_reuses_completed_tool_and_includes_instruction_once(self):
        effects = []
        async def act(arguments):
            effects.append(1)
        component = RuntimeTools(ToolRegistry([Tool("act", "test", {"type": "object"}, act)]))
        model = Model([chunk(calls=[call("{}", name="act")], finish="tool_calls")],
                      [ConnectionError("offline")], [chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model, components=[component])
        await session.run.steer(run.id, "correction")
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "failed")
        resumed = await (await session.run.resume(run.id, engine="worker")).wait(timeout=10)
        self.assertEqual(resumed.data.status, "completed", resumed.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual([m["content"] for m in model.requests[-1]["messages"]].count("correction"), 1)
        tool_steps = [s for s in await resumed.steps.alist() if s.kind == "tool"]
        self.assertEqual(len(tool_steps), 1)
        self.assertTrue(tool_steps[0].metadata["reused"])

    async def test_cancelled_steer_caller_does_not_orphan_persisted_instruction(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        model = Model([chunk("draft", finish="stop")], [chunk("done", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        store = app.project_manager.sessions.conversations(session.data)
        create = store.create
        def blocked(*args, **kwargs):
            value = create(*args, **kwargs)
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Test storage gate was not released")
            return value
        with patch.object(store, "create", side_effect=blocked):
            caller = asyncio.create_task(session.run.steer(run.id, "saved despite disconnect"))
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            caller.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await caller
        self.assertEqual(len(await run.ainstructions()), 1)
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")
        self.assertEqual((await run.ainstructions())[0].status, "applied")
        self.assertEqual(len(await session.run.alist()), 1)

    async def test_memory_summary_keeps_instruction_with_original_turn(self):
        import json
        from llm.components.memory import MemoryComponent
        from tests.llm.configuration_fixtures import memory_processing
        summaries = []
        def auxiliary(**request):
            summaries.append(json.loads(request["messages"][1]["content"]))
            yield chunk('{"summary":"first request and its correction"}', finish="stop")
        component = MemoryComponent(completion_fn=auxiliary)
        config = {"parameters": {"components": {"memory": {"processing": memory_processing({
            "summarize": True, "recall": False, "keep_turns": 1, "summary_after_chars": 1,
            "completion": {"model": "test/aux"}})}}}}
        model = Model([chunk("draft", finish="stop")], [chunk("answer", finish="stop")],
                      [chunk("second answer", finish="stop")], [chunk("third answer", finish="stop")])
        _, session, request, run = await self.setup_backend(model, config=config, components=[component])
        await session.run.steer(run.id, "correction")
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")
        second = await (await session.run.submit("second", engine="worker")).wait(timeout=10)
        self.assertEqual(second.data.status, "completed", second.data.error)
        self.assertEqual(summaries, [])
        third = await (await session.run.submit("third", engine="worker")).wait(timeout=10)
        self.assertEqual(third.data.status, "completed", third.data.error)
        self.assertEqual([m["content"] for m in summaries[0]["messages"]], ["original", "correction", "answer"])

    async def test_memory_compaction_cannot_remove_live_instruction(self):
        from llm.components.memory import MemoryComponent
        from tests.llm.configuration_fixtures import memory_processing
        def auxiliary(**request):
            yield chunk('{"summary":"completed work"}', finish="stop")
        component = MemoryComponent(completion_fn=auxiliary)
        config = {"parameters": {"components": {"memory": {"processing": memory_processing({
            "summarize": True, "recall": False, "active_keep_iterations": 1, "summary_after_chars": 1,
            "completion": {"model": "test/aux"}})}}}}
        effects = []
        async def act(arguments):
            effects.append(1)
            return "result" * 100
        tools = RuntimeTools(ToolRegistry([Tool("act", "test", {"type": "object"}, act)]))
        model = Model([chunk("draft", finish="stop")],
            [chunk(calls=[call("{}", name="act", call_id="one")], finish="tool_calls")],
            [chunk(calls=[call("{}", name="act", call_id="two")], finish="tool_calls")],
            [chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model, config=config, components=[component, tools])
        await session.run.steer(run.id, "keep this instruction")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual(effects, [1, 1])
        self.assertIn("keep this instruction", [m["content"] for m in model.requests[-1]["messages"]])
        self.assertEqual(len([m for m in model.requests[-1]["messages"] if m["role"] == "tool"]), 2)

    async def test_steering_cannot_bypass_run_model_call_limit(self):
        model = Model([chunk("draft", finish="stop")])
        _, session, request, run = await self.setup_backend(model,
            config={"policies": {"usage": {"max_calls": 1}}})
        await session.run.steer(run.id, "one more request")
        model.release.set()
        result = await request.wait(timeout=10)
        self.assertEqual(result.data.status, "failed")
        self.assertEqual(result.data.error_code, "usage_limit")
        self.assertEqual(len(model.requests), 1)

    async def test_completion_budget_removes_entire_prior_steered_turn(self):
        counter = lambda r: sum(len(m.get("content") or "") for m in r["messages"])
        services = ServiceConfig(token_counters={"chars": counter})
        model = Model([chunk("draft", finish="stop")], [chunk("answer", finish="stop")],
                      [chunk("done", finish="stop")])
        _, session, request, run = await self.setup_backend(model, services=services,
            config={"policies": {"completion": {"max_tokens": 30, "counter": "chars"}}})
        await session.run.steer(run.id, "correction")
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "completed")
        next_prompt = "long next question!"
        result = await (await session.run.submit(next_prompt, engine="worker")).wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual(model.requests[-1]["messages"], [{"role": "user", "content": next_prompt}])

    async def test_session_clone_keeps_steered_turn_without_creating_extra_requests(self):
        model = Model([chunk("draft", finish="stop")], [chunk("answer", finish="stop")],
                      [chunk("next answer", finish="stop")])
        _, session, request, run = await self.setup_backend(model,
            config={"policies": {"context": {"mode": "recent", "max_turns": 1}}})
        await session.run.steer(run.id, "correction")
        model.release.set()
        await request.wait(timeout=10)
        await session.run.shutdown()
        cloned = await session.aclone()
        result = await (await cloned.run.submit("next", engine="worker")).wait(timeout=10)
        self.assertEqual(result.data.status, "completed", result.data.error)
        self.assertEqual(len(await cloned.run.alist()), 1)
        self.assertEqual([m["content"] for m in model.requests[-1]["messages"]],
                         ["original", "correction", "answer", "next"])

    async def test_changed_queued_resume_fails_its_run_without_stopping_session_worker(self):
        from llm.engines.base import BaseEngine, EngineEvent
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(context):
            entered.set()
            await release.wait()
        model = Model([ConnectionError("offline")], [chunk("next answer", finish="stop")])
        app, session, request, run = await self.setup_backend(model)
        model.release.set()
        self.assertEqual((await request.wait(timeout=10)).data.status, "failed")
        app.engines.register("hold", BaseEngine(action=hold))
        blocking = await session.run.submit("hold", engine="hold")
        await asyncio.wait_for(entered.wait(), 10)
        try:
            resumed = await session.run.resume(run.id, engine="worker")
            await app._storage_call(app.run_repository.record_checkpoint, await run.aget_data(),
                EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "loop", "operation": "record",
                    "key": "changed", "value": {"status": "completed"}}))
            following = await session.run.submit("next request", engine="worker")
        finally:
            release.set()
        self.assertEqual((await blocking.wait(timeout=10)).data.status, "completed")
        failed = await resumed.wait(timeout=10)
        self.assertEqual(failed.data.status, "failed")
        self.assertIn("changed after admission", failed.data.error)
        self.assertEqual((await following.wait(timeout=10)).data.status, "completed")
        self.assertEqual(len(model.requests), 2)

    def test_context_does_not_mix_instructions_from_sibling_resume_branches(self):
        from llm.core.models import Message, MessageRole, MessageStatus
        from llm.services.history.context import ConversationContextBuilder, ContextPolicy
        def user(identifier, run_id, **metadata):
            return Message(identifier, MessageRole.USER, identifier, MessageStatus.COMMITTED,
                           run_id=run_id, metadata=metadata)
        def answer(run_id):
            return Message(run_id + "_answer", MessageRole.ASSISTANT, run_id + "_answer",
                           MessageStatus.COMPLETED, run_id=run_id)
        def instruction(run_id):
            application = {"run_id": run_id, "boundary": "ready", "target_id": "root",
                           "step_id": None, "time": "2026-10-03T00:00:00Z"}
            return user(run_id + "_steer", run_id, steering={"run_id": run_id, "input_message_id": "a",
                "status": "applied", "applications": [application], "targets": [
                    {"id": "root", "scope": None, "status": "applied", "applications": [application]}]})
        messages = [user("a", "a"), answer("a"), instruction("a"),
                    user("b", "b", resume={"run_id": "a"}), answer("b"), instruction("b"),
                    user("c", "c", resume={"run_id": "a"}), answer("c"), user("next", "next")]
        history = ConversationContextBuilder().for_run(messages, "next",
                                                       policy=ContextPolicy(mode="recent", max_turns=1))
        self.assertEqual([m.id for m in history], ["c", "a_steer", "c_answer", "next"])
        self.assertEqual(messages[2].run_id, "a")
