"""서비스 확장 지점이 실제 실행/조회/종료 경로에 반영되는지 확인한다."""

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from copy import deepcopy

from llm.llm import LargeLanguageModel
from llm.components.base import Component
from llm.components.tools import ToolComponent
from llm.core.models import Message, MessageRole, MessageStatus, RunStatus
from llm.core.paths import RunPaths, StepPaths
from llm.engines.base import BaseEngine, EngineEvent
from llm.engines.registry import EngineRegistry
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.services.configuration import ServiceConfig
from llm.services.history.context import ContextPolicy, ConversationContextBuilder
from llm.services.runtime.events import EventHandlers
from llm.services.infrastructure.logging import DomainLogger, LogSettings
from llm.services.query import Query
from llm.services.runtime.runs import RunRepository
from llm.services.lifecycle.steps import StepRepository


class Echo(BaseEngine):
    async def run(self, context):
        yield context.messages[-1].content


class ExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def backend(self, *, engines=None, components=(), services=None, name="workspace"):
        app = LargeLanguageModel(self.root / name, engines=engines or {"echo": Echo()},
                                 components=components, services=services)
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("test", components=[item.name for item in components])
        session = await project.sessions.acreate("test")
        return app, project, session

    async def run_one(self, session, text="hello", engine="echo"):
        request = await session.run.submit(text, engine=engine)
        return await asyncio.wait_for(request.wait(), 5)

    async def test_unused_tool_does_not_break_text_engine(self):
        app, project, session = await self.backend(components=[ToolComponent()])
        tools = await project.components.aget("tools")
        await tools.aconfigure({"enabled": ["missing"]})
        run = await self.run_one(session)
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)

    async def test_capabilities_are_requested_once_and_shared_across_pipeline(self):
        calls = []
        class Documents(Component):
            name = directory = "documents"
            capabilities = ("documents", "unused")
            def resolve(self, project, capability):
                calls.append(capability)
                return {"value": "document"}
        class Read(Echo):
            required_capabilities = ("documents",)
            async def run(self, context):
                self.last_context = context
                yield context.capabilities["documents"][0]["value"]
        reader = Read()
        pipeline = PipelineEngine([reader, reader])
        app, project, session = await self.backend(components=[Documents()], engines={"read": pipeline})
        run = await self.run_one(session, engine="read")
        self.assertEqual((await run.aresponse()).content, "document")
        self.assertEqual(calls, ["documents"])
        self.assertEqual(reader.last_context.tools.names(), ())

    async def test_missing_required_capability_fails_before_engine(self):
        class Read(Echo):
            required_capabilities = ("documents",)
        _, _, session = await self.backend(engines={"read": Read()})
        run = await self.run_one(session, engine="read")
        self.assertEqual((await run.aresult()).error_code, "capability_failed")
        self.assertEqual(await run.steps.alist(), [])

    async def test_invalid_capability_declaration_is_rejected(self):
        engine = Echo()
        engine.required_capabilities = ["tools"]
        with self.assertRaises(ValueError):
            EngineRegistry().register("invalid", engine)

    async def test_custom_handler_persists_and_returns_state_before_observation(self):
        class Validate(BaseEngine):
            async def run(self, context):
                yield EngineEvent("validation.request", metadata={"subject": "input"})
                yield context.state["validated"]
        handlers = EventHandlers()
        async def validate(context, event):
            await asyncio.sleep(0)
            await context.update_metadata({"validation": event.metadata["subject"]})
            context.engine_context.state["validated"] = "approved"
        handlers.register("validation.request", validate)
        app, _, session = await self.backend(engines={"validate": Validate()}, services=ServiceConfig(event_handlers=handlers))
        seen = []
        app.events.subscribe(lambda run, event: seen.append((event.type, run.metadata.copy())))
        run = await self.run_one(session, engine="validate")
        self.assertEqual((await run.aresponse()).content, "approved")
        self.assertEqual((await run.aget_data()).metadata["validation"], "input")
        self.assertEqual(next(data for kind, data in seen if kind == "validation.request")["validation"], "input")
        self.assertEqual(len(await run.steps.alist()), 1)

    async def test_unknown_or_failed_custom_events_fail_run_and_next_request_continues(self):
        class Unknown(BaseEngine):
            async def run(self, context):
                yield EngineEvent("custom.unknown")
        _, _, session = await self.backend(engines={"unknown": Unknown(), "echo": Echo()})
        failed = await self.run_one(session, engine="unknown")
        self.assertEqual((await failed.aresult()).status, RunStatus.FAILED)
        good = await self.run_one(session)
        self.assertEqual((await good.aresult()).status, RunStatus.COMPLETED)

    async def test_event_metadata_rejects_entire_update_of_service_owned_fields(self):
        class Inspect(BaseEngine):
            async def run(self, context):
                yield EngineEvent("custom.metadata")
                yield "done"
        handlers = EventHandlers()
        async def inspect(context, event):
            before = context.run.metadata
            for key in ("policies", "completions", "resume", "checkpoints", "output", "engine_options"):
                with self.subTest(key=key):
                    with self.assertRaisesRegex(ValueError, key):
                        await context.update_metadata({"partial": True, key: {}})
                    self.assertEqual(context.run.metadata, before)
            await context.update_metadata({"validation": {"accepted": True}})
        handlers.register("custom.metadata", inspect)
        _, _, session = await self.backend(engines={"inspect": Inspect()},
                                            services=ServiceConfig(event_handlers=handlers))
        run = await self.run_one(session, engine="inspect")
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        data = (await run.aget_data()).metadata
        self.assertNotIn("partial", data)
        self.assertEqual(data["validation"], {"accepted": True})

    async def test_event_metadata_cannot_remove_run_usage_limit(self):
        calls = []
        def completion(**kwargs):
            calls.append(kwargs)
            return iter([{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}])
        class Twice(BaseEngine):
            async def run(self, context):
                for index in range(2):
                    if index:
                        yield EngineEvent("custom.metadata")
                    async for event in self.stream_completion({"model": "fixture", "messages": []}):
                        yield event
        handlers = EventHandlers()
        async def inspect(context, event):
            with self.assertRaisesRegex(ValueError, "policies"):
                await context.update_metadata({"policies": {}})
        handlers.register("custom.metadata", inspect)
        _, project, session = await self.backend(engines={"twice": Twice(completion_fn=completion)},
                                                 services=ServiceConfig(event_handlers=handlers))
        await project.aconfigure_policies({"usage": {"max_calls": 1}})
        run = await self.run_one(session, engine="twice")
        self.assertEqual((await run.aresult()).error_code, "usage_limit")
        self.assertEqual(len(calls), 1)
        self.assertEqual((await run.aget_data()).metadata["policies"], {"usage": {"max_calls": 1}})

    async def test_event_metadata_save_and_commit_failures_restore_memory_and_disk(self):
        from llm.services.infrastructure import transactions
        from llm.services.infrastructure.storage import read_json
        class Inspect(BaseEngine):
            async def run(self, context):
                yield EngineEvent("custom.metadata")
                yield "done"
        handlers = EventHandlers()
        async def inspect(context, event):
            await context.update_metadata({"validation": {"accepted": False}})
            before = deepcopy(context.run.metadata)
            path = context.run.paths.root / "run.json"
            disk_before = read_json(path)
            original_save, original_write = context._repository.save, transactions._write
            def fail_save(run):
                original_save(run)
                raise OSError("metadata save failed")
            def fail_commit(path, data):
                if path.name == "COMMITTED":
                    raise OSError("metadata commit failed")
                return original_write(path, data)
            for target, name, failure in ((context._repository, "save", fail_save),
                                          (transactions, "_write", fail_commit)):
                with self.subTest(boundary=name):
                    with patch.object(target, name, side_effect=failure):
                        with self.assertRaises(OSError):
                            await context.update_metadata({"validation": {"accepted": True}, "temporary": True})
                    self.assertEqual(context.run.metadata, before)
                    self.assertEqual(read_json(path), disk_before)
            await context.update_metadata({"checked": True})
        handlers.register("custom.metadata", inspect)
        _, _, session = await self.backend(engines={"inspect": Inspect()},
                                            services=ServiceConfig(event_handlers=handlers))
        run = await self.run_one(session, engine="inspect")
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        data = (await run.aget_data()).metadata
        self.assertEqual(data["validation"], {"accepted": False})
        self.assertNotIn("temporary", data)
        self.assertTrue(data["checked"])

    async def test_validation_failure_is_terminal_and_does_not_notify_success(self):
        class Validate(BaseEngine):
            async def run(self, context):
                yield EngineEvent("validate.input")
                yield "must not run"
        handlers = EventHandlers()
        async def reject(context, event):
            await context.update_metadata({"validation": "rejected"})
            raise ValueError("Rejected input")
        handlers.register("validate.input", reject)
        app, _, session = await self.backend(engines={"validate": Validate()}, services=ServiceConfig(event_handlers=handlers))
        states = []
        app.events.subscribe(lambda event: states.append(event.type), channel="run")
        run = await self.run_one(session, engine="validate")
        await session.run.wait_idle()
        self.assertEqual(states, ["started", "failed"])
        self.assertEqual((await run.aget_data()).metadata["validation"], "rejected")
        self.assertEqual((await run.aresponse()).content, "")

    async def test_queued_delivery_has_bounded_backpressure(self):
        from llm.services.runtime.events import EventSubscriptions
        subscriptions = EventSubscriptions()
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def observe(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscriptions.subscribe(observe, channel="run", delivery="queued", buffer_size=1)
        await subscriptions.publish("run", 1)
        await asyncio.wait_for(entered.wait(), 1)
        await subscriptions.publish("run", 2)
        pending = asyncio.create_task(subscriptions.publish("run", 3))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        release.set()
        await asyncio.wait_for(pending, 1)
        await subscriptions.close()
        self.assertEqual(seen, [1, 2, 3])

    async def test_async_event_handler_can_be_interrupted(self):
        entered = asyncio.Event()
        class AwaitApproval(BaseEngine):
            async def run(self, context):
                yield EngineEvent("approval.request")
        handlers = EventHandlers()
        async def approval(context, event):
            entered.set()
            await asyncio.Event().wait()
        handlers.register("approval.request", approval)
        _, _, session = await self.backend(engines={"approval": AwaitApproval()}, services=ServiceConfig(event_handlers=handlers))
        request = await session.run.submit("hello", engine="approval")
        await asyncio.wait_for(entered.wait(), 5)
        await session.run.interrupt()
        run = await request.wait()
        self.assertEqual((await run.aresult()).status, RunStatus.INTERRUPTED)

    async def test_reserved_or_duplicate_event_handler_is_rejected(self):
        handlers = EventHandlers()
        with self.assertRaises(ValueError):
            handlers.register("text_delta", lambda *_: None)
        handlers.register("custom.event", lambda *_: None)
        with self.assertRaises(ValueError):
            handlers.register("custom.event", lambda *_: None)

    async def test_multiple_observers_queue_thread_and_shutdown_drain(self):
        app, _, session = await self.backend()
        main_thread = threading.get_ident()
        threads, states, text = [], [], []
        def sync_observer(run, event):
            threads.append(threading.get_ident())
        async def async_observer(event):
            await asyncio.sleep(0.001)
            states.append(event.type)
        app.events.subscribe(sync_observer, delivery="queued", buffer_size=2)
        app.events.subscribe(async_observer, channel="run", delivery="queued")
        unsubscribe = app.events.subscribe(lambda run, event: text.append(event.delta.text) if event.delta else None)
        await self.run_one(session)
        unsubscribe()
        await app.shutdown()
        self.assertTrue(threads)
        self.assertTrue(all(thread != main_thread for thread in threads))
        self.assertEqual(states, ["started", "completed"])
        self.assertIn("hello", text)
        with self.assertRaises(RuntimeError):
            app.events.subscribe(lambda *_: None)

    async def test_observer_failure_does_not_stop_other_observers(self):
        logs, seen = [], []
        logger = DomainLogger(sink=lambda path, event, fields: logs.append(event))
        app, _, session = await self.backend(services=ServiceConfig(logger=logger))
        def broken(*args):
            raise ValueError("observer failed")
        app.events.subscribe(broken, delivery="queued")
        app.events.subscribe(lambda *args: seen.append(True))
        run = await self.run_one(session)
        await app.events.flush()
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        self.assertTrue(seen)
        self.assertIn("observer.failed", logs)

    async def test_session_details_survive_active_run_and_configuration_stays_guarded(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(context):
            entered.set()
            await release.wait()
        _, _, session = await self.backend(engines={"held": PreparationStep("held", wait)})
        request = await session.run.submit("hello", engine="held")
        await asyncio.wait_for(entered.wait(), 5)
        try:
            await session.asave(title="renamed", metadata={"label": "work"})
            with self.assertRaises(ValueError):
                await session.asave(config={"data": {"new": True}})
        finally:
            release.set()
        await request.wait()
        await session.run.wait_idle()
        self.assertEqual((await session.aget_data()).title, "renamed")
        self.assertEqual((await session.aget_data()).metadata, {"label": "work"})

    async def test_shared_custom_repositories_are_used_by_all_facade_queries(self):
        class CustomRuns(RunRepository):
            def paths(self, session, run_id):
                return RunPaths(session.paths.runs / "custom" / run_id)
            def list(self, session):
                return [self.load(session, path.parent.name) for path in (session.paths.runs / "custom").glob("*/run.json")]
        class CustomSteps(StepRepository):
            def paths(self, run, step_id):
                return StepPaths(run.paths.steps / "custom" / step_id)
            def list(self, run):
                return [self.load(run, path.parent.name) for path in (run.paths.steps / "custom").glob("*/step.json")]
        runs, steps = CustomRuns(), CustomSteps()
        app, project, session = await self.backend(services=ServiceConfig(run_repository=runs, step_repository=steps))
        run = await self.run_one(session)
        self.assertIs(app.run_repository, runs)
        self.assertEqual((await session.run.aload(run.id)).id, run.id)
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertEqual(len(await session.results.alist()), 1)
        self.assertEqual(len(await project.results.alist()), 1)
        self.assertEqual(app.project_manager.sessions.results.load(await session.aget_data(), run.id).run_id, run.id)
        records = await run.steps.alist()
        self.assertEqual(len(records), 1)
        self.assertEqual((await run.steps.aload(records[0].id)).id, records[0].id)

    async def test_queries_apply_cursor_status_offset_limit_and_direction(self):
        class Fail(BaseEngine):
            async def run(self, context):
                raise ValueError("fail")
        app, project, session = await self.backend(engines={"echo": Echo(), "fail": Fail()})
        one = await self.run_one(session, "one")
        two = await self.run_one(session, "two", engine="fail")
        three = await self.run_one(session, "three")
        self.assertEqual([r.id for r in await session.run.alist(query=Query(after=one.id, status="completed", limit=1))], [three.id])
        self.assertEqual([r.run_id for r in await session.results.alist(query=Query(status="failed"))], [two.id])
        self.assertEqual([r.id for r in await session.run.alist(query=Query(descending=True, offset=1, limit=1))], [two.id])
        self.assertEqual(await session.run.alist(query=Query(limit=0)), [])
        self.assertEqual(len(await session.aconversation(query=Query(status="committed", limit=2))), 2)
        self.assertEqual(len(await run_steps(one, Query(limit=1))), 1)
        self.assertEqual(len(await app.projects.alist(query=Query(status="active", limit=1))), 1)
        self.assertEqual(len(await project.sessions.alist(query=Query(status="idle", limit=1))), 1)
        with self.assertRaises(ValueError):
            await session.run.alist(query=Query(after="missing"))

    async def test_project_context_policy_and_component_binding(self):
        seen = []
        class Inspect(Echo):
            async def run(self, context):
                seen.append([item.content for item in context.messages])
                yield "answer"
        app, project, session = await self.backend(engines={"echo": Inspect()}, components=[ToolComponent()])
        await project.aconfigure_policies({"context": {"mode": "recent", "max_turns": 1}})
        for text in ("one", "two", "three"):
            await self.run_one(session, text)
        self.assertEqual(seen[-1], ["two", "answer", "three"])
        tools = await project.components.aget("tools")
        self.assertEqual(await tools.aenabled(), [])
        await app.shutdown()
        with self.assertRaises(RuntimeError):
            tools.enabled()

    async def test_logger_injection_is_isolated_and_file_defaults_remain(self):
        seen = []
        app, project, session = await self.backend(services=ServiceConfig(logger=DomainLogger(sink=lambda *args: seen.append(args))))
        other, default_project, _ = await self.backend(name="other")
        await self.run_one(session)
        self.assertTrue(seen)
        self.assertFalse((project.paths.logs / "service.log").exists())
        self.assertTrue((default_project.paths.logs / "service.log").exists())
        self.assertTrue(all(str(path).startswith(str(app.workspace)) for path, _, _ in seen))


async def run_steps(run, query):
    return await run.steps.alist(query=query)


class ContextPolicyTests(unittest.TestCase):
    def messages(self):
        return [
            Message("u1", MessageRole.USER, "one", MessageStatus.COMMITTED, run_id="r1"),
            Message("a1", MessageRole.ASSISTANT, "ok", MessageStatus.COMPLETED, run_id="r1"),
            Message("u2", MessageRole.USER, "two", MessageStatus.COMMITTED, run_id="r2"),
            Message("a2", MessageRole.ASSISTANT, "partial", MessageStatus.FAILED, run_id="r2"),
            Message("u3", MessageRole.USER, "now", MessageStatus.COMMITTED, run_id="r3"),
        ]

    def test_full_completed_recent_and_budget_preserve_current_input(self):
        def ids(policy):
            return [item.id for item in ConversationContextBuilder(policy).for_run(self.messages(), "u3")]
        self.assertEqual(ids(ContextPolicy()), ["u1", "a1", "u2", "a2", "u3"])
        self.assertEqual(ids(ContextPolicy("completed")), ["u1", "a1", "u3"])
        self.assertEqual(ids(ContextPolicy("recent", max_turns=1)), ["u2", "a2", "u3"])
        self.assertEqual(ids(ContextPolicy("recent_completed", max_turns=1)), ["u1", "a1", "u3"])
        self.assertEqual(ids(ContextPolicy("budget", max_chars=3)), ["u3"])
        with self.assertRaises(ValueError):
            ids(ContextPolicy("budget", max_chars=2))

    def test_invalid_query_and_policy_settings(self):
        for settings in ({"limit": -1}, {"offset": True}, {"after": ""}, {"descending": 1}):
            with self.assertRaises(ValueError):
                Query(**settings)
        with self.assertRaises(ValueError):
            ContextPolicy("budget")
        with self.assertRaises(ValueError):
            ContextPolicy("unknown")
        with self.assertRaises(ValueError):
            LogSettings(backup_count=0)
