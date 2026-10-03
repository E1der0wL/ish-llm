"""운영 정책을 실제 Facade/Run/Step/Graph 경로로 검증한다. 외부 모델은 호출하지 않는다."""

import asyncio
import gc
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from tests.llm.support.runtime_tools import RuntimeTools
from llm.components.tools import Tool, ToolComponent, ToolRegistry
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.agents import AgentComponent
from llm.core.models import MessageRole, MessageStatus, RunStatus, StepStatus
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.engines.graph.agent import AgentNode
from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.history.context import CompletionPolicy
from llm.services.history.conversation import ConversationStore, MemoryConversationStore
from llm.services.runtime.events import EventSubscriptions
from llm.services.runtime.policies import ExecutionLimitError, RunLimits
from llm.services.runtime.runs import RunRequestError
from llm.services.runtime.tools import ToolPolicy
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.llm.test_graph_engine import parallel


class Echo(BaseEngine):
    async def run(self, context):
        yield context.messages[-1].content


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.effects = []

        async def action(arguments):
            self.effects.append(arguments)
            return "done"

        self.tools = RuntimeTools(ToolRegistry([Tool("act", "act", {"type": "object"}, action)]))

    async def backend(self, *, engines=None, components=(), services=None, name="workspace", policies=None):
        app = LargeLanguageModel(self.root / name, engines=engines or {"echo": Echo()},
                                 components=components, services=services)
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate("test", components=[item.name for item in components],
                                            config={"policies": policies or {}})
        if self.tools in components:
            await (await project.components.aget("tools")).aenable("act")
        return app, project, await project.sessions.acreate("test")

    async def execute(self, session, engine="echo", text="hello", **options):
        request = await session.run.submit(text, engine=engine, **options)
        return await request.wait(timeout=15)

    def loop(self, *responses):
        completion = ScriptedCompletion(*responses)
        return LoopEngine(completion_kwargs={"model": "test/model"}, completion_fn=completion), completion

    async def test_queue_limit_cancel_and_status_do_not_interrupt_active_run(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class Slow(Echo):
            async def run(self, context):
                entered.set()
                await release.wait()
                yield "done"

        app, _, session = await self.backend(engines={"slow": Slow(), "echo": Echo()},
                                         policies={"run": {"max_queued": 1}})
        first = await session.run.submit("first", engine="slow")
        await asyncio.wait_for(entered.wait(), 5)
        second = await session.run.submit("second", engine="echo")
        with self.assertRaises(RunRequestError) as raised:
            await session.run.submit("overflow", engine="echo")
        self.assertEqual(raised.exception.code, "queue_full")
        with patch.object(app.run_repository, "list", side_effect=AssertionError("history scan")):
            status = await session.run.astatus(queued_limit=1)
        self.assertEqual(status.queued_request_ids, [second.id])
        self.assertEqual(status.queued_count, 1)
        self.assertEqual(status.engine, "slow")
        waiter = asyncio.create_task(second.wait())
        self.assertTrue(await second.cancel())
        with self.assertRaises(RunRequestError) as raised:
            await asyncio.wait_for(waiter, 5)
        self.assertEqual(raised.exception.code, "request_cancelled")
        self.assertFalse(await first.cancel())
        self.assertFalse(await second.cancel())
        third = await session.run.submit("third", engine="echo")
        release.set()
        self.assertEqual((await first.wait()).data.status, RunStatus.COMPLETED)
        self.assertEqual((await (await third.wait()).aresponse()).content, "third")
        self.assertEqual(len(await session.run.alist()), 2)
        self.assertEqual((await session.run.astatus()).queued_count, 0)

    async def test_cancel_survives_file_restart_without_starting_runtime(self):
        app, project, session = await self.backend()
        store = app.project_manager.sessions.conversations(session.data)
        with app.project_manager.ownership.scope():
            message = store.create(MessageRole.USER, "cancel", MessageStatus.QUEUED, metadata={"engine": "echo"})
        request = await session.run.arequest(message.id)
        self.assertTrue(await request.cancel())
        await app.shutdown()
        other = LargeLanguageModel(self.root / "workspace", components=[], engines={"echo": Echo()})
        self.addAsyncCleanup(other.shutdown)
        reopened = (await other.projects.aload(project.id)).sessions.load(session.id)
        await reopened.run.start()
        await reopened.run.wait_idle()
        self.assertEqual(await reopened.run.alist(), [])
        self.assertEqual((await (await reopened.run.arequest(message.id)).aget_data()).status, MessageStatus.CANCELLED)

    async def test_run_deadline_finishes_steps_and_continues_queue(self):
        entered = asyncio.Event()

        class Stuck(Echo):
            async def run(self, context):
                entered.set()
                yield "partial"
                await asyncio.Event().wait()

        _, _, session = await self.backend(engines={"stuck": Stuck(), "echo": Echo()},
            policies={"run": {"timeout_seconds": 1}})
        first = await session.run.submit("first", engine="stuck")
        await asyncio.wait_for(entered.wait(), 5)
        second = await session.run.submit("second", engine="echo")
        failed = await first.wait(timeout=10)
        self.assertEqual(failed.result.error_code, "run_timeout")
        self.assertEqual(failed.response.content, "partial")
        self.assertTrue(all(step.status == StepStatus.FAILED for step in failed.steps.list()))
        self.assertEqual((await second.wait()).data.status, RunStatus.COMPLETED)

    async def test_loop_tool_denial_is_persisted_without_effects(self):
        loop, completion = self.loop([chunk(calls=[call("{}", name="act")], finish="tool_calls")])
        _, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
            services=ServiceConfig(tool_policy=ToolPolicy(allowed_tools=())))
        run = await self.execute(session, "loop")
        self.assertEqual(run.result.error_code, "tool_denied")
        step = next(item for item in run.steps.list() if item.kind == "tool")
        self.assertEqual(step.metadata["error_code"], "tool_denied")
        self.assertEqual(step.status, StepStatus.FAILED)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(completion.requests), 1)

    async def test_approval_phase_and_injected_runner_are_recorded_before_effect(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def authorize(call):
            calls.append(call)
            entered.set()
            await release.wait()
            call.arguments["changed"] = True
            return True

        async def runner(tool, call):
            active = await session.run.aload(call.run_id)
            steps = await active.steps.alist()
            recorded = next(step for step in steps if step.id == call.step_id)
            self.assertEqual(recorded.metadata["authorization"], "allowed")
            self.assertNotIn("changed", call.arguments)
            return "isolated result"

        loop, _ = self.loop([chunk(calls=[call("{}", name="act")], finish="tool_calls")],
                            [chunk("answer"), chunk(finish="stop")])
        _, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize, runner=runner)))
        request = await session.run.submit("hello", engine="loop")
        await asyncio.wait_for(entered.wait(), 5)
        active = await request.aget_run()
        steps = await active.steps.alist()
        self.assertEqual(next(item for item in steps if item.kind == "tool").metadata["phase"], "authorizing")
        release.set()
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertEqual(self.effects, [])
        self.assertEqual(calls[0].session_id, session.id)

    async def test_tool_approval_timeout_has_no_side_effect(self):
        async def authorize(call):
            await asyncio.Event().wait()

        completion = ScriptedCompletion([chunk(calls=[call("{}", name="act")], finish="tool_calls")])
        loop = LoopEngine(tool_timeout=.1, completion_kwargs={"model": "test/model"}, completion_fn=completion)
        _, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize)))
        run = await self.execute(session, "loop")
        self.assertEqual(run.result.error_code, "tool_timeout")
        self.assertEqual(self.effects, [])

    async def test_authorization_record_failure_prevents_tool_effect(self):
        from llm.engines.base import EngineEventType
        for batch_size in (1, 4):
            with self.subTest(batch_size=batch_size):
                loop, _ = self.loop([chunk('prelude'), chunk(calls=[call("{}", name="act")], finish="tool_calls")])
                app, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
                    name=f'batch-{batch_size}', policies={'output': {'batch_size': batch_size}})
                save = app.step_manager.repository.save
                seen = []
                app.events.subscribe(lambda run, event: seen.append(event))
                def failing_save(step):
                    if step.metadata.get("phase") == "executing":
                        raise OSError("disk unavailable")
                    return save(step)

                with patch.object(app.step_manager.repository, "save", side_effect=failing_save):
                    run = await self.execute(session, "loop")
                self.assertEqual(run.data.status, RunStatus.FAILED)
                self.assertEqual(self.effects, [])
                self.assertFalse(any(event.type == EngineEventType.STEP_UPDATED and
                    event.metadata.get('authorization') == 'allowed' for event in seen))

    async def test_interrupt_while_awaiting_approval_does_not_execute_tool(self):
        entered = asyncio.Event()

        async def authorize(call):
            entered.set()
            await asyncio.Event().wait()

        loop, _ = self.loop([chunk(calls=[call("{}", name="act")], finish="tool_calls")])
        _, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
            services=ServiceConfig(tool_policy=ToolPolicy(authorize=authorize)))
        request = await session.run.submit("hello", engine="loop")
        await asyncio.wait_for(entered.wait(), 5)
        self.assertTrue(await session.run.interrupt())
        run = await request.wait(timeout=5)
        self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
        step = next(item for item in await run.steps.alist() if item.kind == "tool")
        self.assertEqual(step.status, StepStatus.INTERRUPTED)
        self.assertEqual(self.effects, [])

    async def test_file_cache_reuses_projection_and_evicts_without_losing_history(self):
        app, project, session = await self.backend(services=ServiceConfig(conversation_cache_size=1))
        run = await self.execute(session)
        factory = app.project_manager.sessions.conversations
        store = factory(session.data)
        self.assertIs(store, app._manager(session._snapshot)._store(session.data))
        # 재조회마다 처음부터 JSONL을 재생하지 않는다. stat 기반 외부 변경 감지는 유지한다.
        with patch.object(store, "_apply", side_effect=AssertionError("unexpected replay")):
            self.assertEqual((await session.run.astatus()).queued_count, 0)
            self.assertEqual((await run.aresponse()).content, "hello")
        other = await project.sessions.acreate("other")
        factory(other.data)
        reopened = factory(session.data)
        self.assertIsNot(store, reopened)
        self.assertEqual(reopened.get(run.data.assistant_message_id).content, "hello")
        with app.project_manager.ownership.scope():
            external = ConversationStore(session.data.paths.conversation)
            external.update_metadata(run.data.assistant_message_id, {"external": True})
        self.assertTrue(reopened.get(run.data.assistant_message_id).metadata["external"])

    async def test_parallel_graph_nodes_share_tool_budget(self):
        graph = parallel()
        for name in ("a", "b"):
            graph["nodes"][name] = {"type": "tool", "tool": "act", "arguments": {}}
        _, project, session = await self.backend(components=[self.tools, WorkflowComponent()],
            engines={"graph": GraphEngine(handlers={"tool": ToolNode()})},
            services=ServiceConfig(tool_policy=ToolPolicy(max_calls=1)))
        await (await project.components.aget("workflows")).acreate(graph, identifier="flow")
        run = await self.execute(session, "graph", engine_options={"workflow": "flow"})
        self.assertEqual(run.result.error_code, "tool_budget_exceeded")
        self.assertLessEqual(len(self.effects), 1)
        self.assertEqual(run.data.status, RunStatus.FAILED)

    async def test_graph_agent_inherits_backend_tool_policy(self):
        completion = ScriptedCompletion([chunk(calls=[call("{}", name="act")], finish="tool_calls")])
        _, project, session = await self.backend(components=[self.tools, WorkflowComponent(), AgentComponent()],
            engines={"graph": GraphEngine(handlers={"agent": AgentNode(engines={"loop": LoopEngine(completion_fn=completion)})})},
            services=ServiceConfig(tool_policy=ToolPolicy(allowed_tools=())))
        await (await project.components.aget("agents")).acreate({"engine": "loop", "purpose": "test", "completion": {"model": "test/model"},
            "tools": ["act"], "engine_options": {"max_iterations": 2}}, identifier="worker")
        graph = WorkflowGraph(entry="a").node("a", "agent", agent="worker").node("end", "end").connect("a", "end").to_dict()
        await (await project.components.aget("workflows")).acreate(graph, identifier="flow")
        run = await self.execute(session, "graph", engine_options={"workflow": "flow"})
        self.assertEqual(run.result.error_code, "tool_denied")
        self.assertEqual(self.effects, [])

    async def test_loop_checks_budget_again_after_tool_result(self):
        loop, completion = self.loop([chunk(calls=[call("{}", name="act")], finish="tool_calls")])
        counter = lambda request: 100 if any(item["role"] == "tool" for item in request["messages"]) else 1
        _, _, session = await self.backend(engines={"loop": loop}, components=[self.tools],
            services=ServiceConfig(token_counters={"test": counter}),
            policies={"completion": {"max_tokens": 10, "counter": "test"}})
        run = await self.execute(session, "loop")
        self.assertEqual(run.result.error_code, "context_budget_exceeded")
        self.assertEqual(len(completion.requests), 1)
        self.assertEqual(len(self.effects), 1)

    async def test_context_counter_runs_outside_event_loop(self):
        import threading
        main_thread = threading.get_ident()
        counted_threads = []
        def count(request):
            counted_threads.append(threading.get_ident())
            return 1
        loop, _ = self.loop([chunk("answer"), chunk(finish="stop")])
        _, _, session = await self.backend(engines={"loop": loop},
            services=ServiceConfig(token_counters={"test": count}),
            policies={"completion": {"max_tokens": 10, "counter": "test"}})
        run = await self.execute(session, "loop")
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
        self.assertTrue(counted_threads)
        self.assertNotIn(main_thread, counted_threads)

    async def test_slow_ui_queue_is_bounded_and_reports_dropped_notifications(self):
        events = EventSubscriptions()
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)

        subscription = events.subscribe(callback, channel="run", delivery="queued", buffer_size=2,
                                        overflow="drop_oldest", callback_timeout=5)
        await events.publish("run", 0)
        await entered.wait()
        for index in range(1, 1001):
            await events.publish("run", index)
        self.assertEqual(subscription.stats["pending"], 2)
        self.assertEqual(subscription.stats["dropped"], 998)
        release.set()
        await events.close()
        self.assertEqual(seen, [0, 999, 1000])
        self.assertEqual(subscription.stats["delivered"], 3)

    async def test_stuck_ui_is_disabled_and_shutdown_completes(self):
        events = EventSubscriptions()

        async def stuck(value):
            await asyncio.Event().wait()

        subscription = events.subscribe(stuck, channel="run", delivery="queued", callback_timeout=.05)
        for index in range(3):
            await events.publish("run", index)
        await asyncio.wait_for(events.close(), 2)
        self.assertEqual(subscription.stats["timed_out"], 1)
        self.assertFalse(subscription.stats["active"])

    async def test_callback_own_timeout_is_an_isolated_error_not_a_subscription_deadline(self):
        events = EventSubscriptions()
        async def callback(value):
            raise asyncio.TimeoutError("UI network timeout")
        subscription = events.subscribe(callback, channel="run", delivery="queued", callback_timeout=1)
        await events.publish("run", 1)
        await events.publish("run", 2)
        await events.flush()
        self.assertEqual(subscription.stats["failures"], 2)
        self.assertEqual(subscription.stats["timed_out"], 0)
        self.assertTrue(subscription.stats["active"])
        await events.close()


class SubscriptionLifetimeTests(unittest.IsolatedAsyncioTestCase):
    """구독 수명과 실행 수명을 구분하고 실제 Task/콜백 참조 회수를 검사한다."""

    async def asyncSetUp(self):
        self.events = EventSubscriptions()
        self.addAsyncCleanup(self.events.close)

    async def test_repeated_unsubscribe_reclaims_workers_and_callbacks(self):
        class Observer:
            async def receive(self, value):
                pass
        handles, references, workers = [], [], []
        for _ in range(20):
            observer = Observer()
            references.append(weakref.ref(observer))
            handle = self.events.subscribe(observer.receive, delivery="queued")
            handles.append(handle)
            await self.events.publish("engine", "value")
            await self.events.flush()
            workers.append(handle._entry["worker"])
            handle()
            await self.events.flush()
            self.assertTrue(workers[-1].done())
            await handle.aclose()
            del observer
        gc.collect()
        self.assertTrue(all(worker.done() for worker in workers))
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(len(self.events._subscriptions), 0)
        self.assertEqual(self.events.stats["delivered"], 20)
        self.assertTrue(all(handle.stats["delivered"] == 1 for handle in handles))
        await self.events.close()
        self.assertEqual(self.events.stats["delivered"], 20)

    async def test_unused_and_inline_subscriptions_release_callbacks(self):
        class Observer:
            def __call__(self, value):
                pass
        for publish in (False, True):
            observer = Observer()
            reference = weakref.ref(observer)
            subscription = self.events.subscribe(observer)
            if publish:
                await self.events.publish("engine", "value")
            subscription()
            subscription()
            await subscription.aclose()
            del observer
            gc.collect()
            self.assertIsNone(reference())
            self.assertFalse(subscription.stats["active"])
        self.assertEqual(self.events.stats["delivered"], 1)

    async def test_unsubscribe_unblocks_publishers_but_finishes_current_callback(self):
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscription = self.events.subscribe(callback, delivery="queued", buffer_size=1)
        await self.events.publish("engine", 0)
        await entered.wait()
        await self.events.publish("engine", 1)
        publishers = [asyncio.create_task(self.events.publish("engine", i)) for i in (2, 3)]
        await asyncio.sleep(0)
        self.assertTrue(all(not task.done() for task in publishers))
        subscription()
        closing = asyncio.create_task(subscription.aclose())
        try:
            await asyncio.wait_for(asyncio.gather(*publishers), 1)
            await self.events.publish("engine", 4)
            self.assertFalse(closing.done())
            self.assertEqual(subscription.stats["pending"], 0)
            self.assertEqual(seen, [])
        finally:
            release.set()
            await asyncio.wait_for(closing, 1)
        self.assertEqual(seen, [0])
        self.assertEqual(subscription.stats["delivered"], 1)
        self.assertEqual(subscription.stats["dropped"], 0)  # 해제는 overflow drop이 아니다.
        await asyncio.wait_for(self.events.flush(), 1)

    async def test_callback_can_unsubscribe_itself_but_cannot_await_own_close(self):
        for delivery in ("inline", "queued"):
            seen = []
            async def callback(value):
                with self.assertRaisesRegex(RuntimeError, "own callback"):
                    await subscription.aclose()
                subscription()
                await asyncio.sleep(0)
                seen.append(value)
            subscription = self.events.subscribe(callback, delivery=delivery)
            await self.events.publish("engine", delivery)
            await self.events.flush()
            await subscription.aclose()
            self.assertEqual(seen, [delivery])
            self.assertEqual(subscription.stats["failures"], 0)

    async def test_inline_unsubscribe_waits_without_cancelling_publisher(self):
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscription = self.events.subscribe(callback)
        publisher = asyncio.create_task(self.events.publish("engine", "current"))
        await entered.wait()
        subscription()
        closing = asyncio.create_task(subscription.aclose())
        await asyncio.sleep(0)
        try:
            await self.events.publish("engine", "ignored")
            self.assertFalse(closing.done())
            self.assertFalse(publisher.done())
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(publisher, closing), 1)
        self.assertEqual(seen, ["current"])
        self.assertEqual(subscription.stats["delivered"], 1)

    async def test_idle_worker_releases_last_event_and_error_callback(self):
        class Payload:
            # 공유 참조를 의도하는 확장 객체도 idle worker가 불필요하게 붙잡지 않는다.
            def __deepcopy__(self, memo):
                return self
        class ErrorObserver:
            def __call__(self):
                pass
        payload, errors = Payload(), ErrorObserver()
        payload_ref, error_ref = weakref.ref(payload), weakref.ref(errors)
        self.events.subscribe(lambda value: None, delivery="queued")
        await self.events.publish("engine", payload, on_error=errors)
        await self.events.flush()
        del payload, errors
        gc.collect()
        self.assertIsNone(payload_ref())
        self.assertIsNone(error_ref())

    async def test_thread_callback_can_unsubscribe_without_cancelling_current_work(self):
        finished = []
        def callback(value):
            subscription()
            finished.append(value)
        subscription = self.events.subscribe(callback, delivery="queued")
        await self.events.publish("engine", "thread")
        await self.events.flush()
        await subscription.aclose()
        self.assertEqual(finished, ["thread"])
        self.assertEqual(subscription.stats["delivered"], 1)

    async def test_timeout_reclaims_worker_and_aggregate_counts_once(self):
        async def callback(value):
            await asyncio.Event().wait()
        reference = weakref.ref(callback)
        subscription = self.events.subscribe(callback, delivery="queued", callback_timeout=.01)
        await self.events.publish("engine", "first")
        await self.events.publish("engine", "discarded")
        await asyncio.wait_for(self.events.flush(), 1)
        await subscription.aclose()
        del callback
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(subscription.stats["timed_out"], 1)
        self.assertEqual(self.events.stats["timed_out"], 1)
        self.assertEqual(len(self.events._subscriptions), 0)
        subscription()
        await self.events.close()
        self.assertEqual(self.events.stats["failures"], 1)

    async def test_cancelling_close_waiter_does_not_cancel_callback_or_other_waiters(self):
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscription = self.events.subscribe(callback, delivery="queued")
        await self.events.publish("engine", "current")
        await entered.wait()
        cancelled = asyncio.create_task(subscription.aclose())
        other = asyncio.create_task(subscription.aclose())
        await asyncio.sleep(0)
        cancelled.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await cancelled
            self.assertFalse(other.done())
        finally:
            release.set()
            await asyncio.wait_for(other, 1)
        self.assertEqual(seen, ["current"])
        self.assertEqual(self.events.stats["delivered"], 1)

    async def test_shutdown_drains_active_subscription_and_joins_concurrent_close(self):
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscription = self.events.subscribe(callback, delivery="queued")
        await self.events.publish("engine", 0)
        await entered.wait()
        await self.events.publish("engine", 1)
        closing = asyncio.create_task(self.events.close())
        other = asyncio.create_task(self.events.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.assertFalse(other.done())
        closing.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await closing
        finally:
            release.set()
            await asyncio.wait_for(other, 1)
        self.assertEqual(seen, [0, 1])
        self.assertFalse(subscription.stats["active"])
        self.assertEqual(len(self.events._subscriptions), 0)
        self.assertEqual(self.events.stats["delivered"], 2)

    async def test_unsubscribe_during_shutdown_discards_pending_only(self):
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        async def callback(value):
            entered.set()
            await release.wait()
            seen.append(value)
        subscription = self.events.subscribe(callback, delivery="queued")
        await self.events.publish("engine", 0)
        await entered.wait()
        await self.events.publish("engine", 1)
        closing = asyncio.create_task(self.events.close())
        await asyncio.sleep(0)
        subscription()
        release.set()
        await asyncio.wait_for(closing, 1)
        await subscription.aclose()
        self.assertEqual(seen, [0])


class BudgetAndProjectionTests(unittest.TestCase):
    def test_long_history_uses_logarithmic_counter_calls(self):
        calls = []
        def count(request):
            calls.append(len(request["messages"]))
            return calls[-1]
        messages = [item for _ in range(1000) for item in (
            {"role": "user", "content": "old"}, {"role": "assistant", "content": "reply"})]
        messages.append({"role": "user", "content": "current"})
        request = CompletionPolicy(8, counter=count).prepare({"messages": messages})
        self.assertEqual(len(request["messages"]), 7)
        self.assertLessEqual(len(calls), 14)

    def test_budget_preserves_system_current_tool_pairs_and_original_request(self):
        request = {"model": "test/model", "tools": [{"x": 1}], "messages": [
            {"role": "system", "content": "rules"}, {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "new"},
            {"role": "assistant", "tool_calls": [{"id": "call"}]},
            {"role": "tool", "tool_call_id": "call", "content": "result"}]}
        seen = []
        def counter(value):
            seen.append(value)
            return len(value["messages"]) + len(value["tools"])
        result = CompletionPolicy(6, reserve_tokens=1, counter=counter).prepare(request)
        self.assertEqual(result["messages"], [request["messages"][0], *request["messages"][3:]])
        result["messages"][0]["content"] = "changed"
        self.assertEqual(request["messages"][0]["content"], "rules")
        self.assertTrue(all(item["tools"] for item in seen))

    def test_current_input_is_never_truncated(self):
        with self.assertRaises(ExecutionLimitError) as raised:
            CompletionPolicy(2, counter=lambda request: 3).prepare({"messages": [{"role": "user", "content": "large"}]})
        self.assertEqual(raised.exception.code, "context_budget_exceeded")

    def test_counts_follow_status_changes_replay_truncation_and_memory_clear(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "conversation.jsonl"
            for store in (MemoryConversationStore(), ConversationStore(path)):
                user = store.create(MessageRole.USER, "request", MessageStatus.QUEUED)
                store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING)
                self.assertEqual(store.count(status=MessageStatus.QUEUED), 1)
                store.set_status(user.id, MessageStatus.CANCELLED)
                self.assertEqual(store.count(status=MessageStatus.QUEUED), 0)
                self.assertEqual(store.count(role=MessageRole.USER), 1)
                self.assertEqual(store.count(), 2)
                if isinstance(store, MemoryConversationStore):
                    store.clear()
                    self.assertEqual(store.count(), 0)
            reopened = ConversationStore(path)
            self.assertEqual(reopened.count(status=MessageStatus.CANCELLED), 1)
            path.write_text("", encoding="utf-8")
            self.assertEqual(reopened.count(), 0)

    def test_invalid_limits_fail_early(self):
        for construct in (lambda: RunLimits(max_queued=0), lambda: RunLimits(timeout_seconds=float("nan")),
                          lambda: ToolPolicy(max_calls=True), lambda: ToolPolicy(allowed_tools=["tool"]),
                          lambda: CompletionPolicy(10, counter=None), lambda: CompletionPolicy(10, reserve_tokens=10, counter=len)):
            with self.assertRaises((ValueError, TypeError)):
                construct()
