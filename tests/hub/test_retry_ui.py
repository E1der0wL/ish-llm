"""Observe actual retry decisions, and render approvals without dumping bodies."""

from llm.core.model_protocol import ModelMessage, ModelRequest

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from contextlib import aclosing
from dataclasses import replace
import tempfile
from time import monotonic
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from hub.backend.interactions.approval import review_lines, review_text
from hub.backend.execution.retries import HubLoopEngine, RetryMonitor
from hub.backend.runtime import HubConfig, HubRuntime
from hub.locales import Language
from hub.model import RetrySummary, RunSummary
from hub.widget.feedback.retry_status import retry_text
from llm.services.infrastructure.observability import Observability
from tests.hub.support import eventually
from tests.hub.test_response_reliability import HTTPFailure
from tests.llm.test_loop import ScriptedCompletion, chunk


class RetryUITests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_backoff_uses_policy_and_clears_on_interrupt(self):
        provider = ScriptedCompletion([HTTPFailure(503)])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))},
                project_config={"parameters": {"engines": {"loop": {"policy": {"provider": {
                    "max_attempts": 3, "delay_seconds": 10, "max_delay_seconds": 2}}}}}}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                request = await runtime.sessions[sid].run.submit("Try", agent_id="assistant")
                monitor = runtime.backend.services.observability_sink
                await eventually(lambda: bool(monitor.states))
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.run.retry.attempt, 1)
                self.assertEqual(snapshot.run.retry.maximum, 3)
                self.assertGreater(snapshot.run.retry.retry_at, monotonic())
                self.assertLessEqual(snapshot.run.retry.retry_at - monotonic(), 2)
                await runtime.sessions[sid].run.interrupt()
                await request.wait()
                self.assertEqual(monitor.states, {})
                self.assertIsNone((await runtime.snapshot()).run.retry)
            finally:
                await runtime.close()

    async def test_attempt_counter_resets_per_completion_and_does_not_leak_context(self):
        provider = ScriptedCompletion([HTTPFailure(429)], [chunk("Done", finish="stop")],
                                      [chunk("Next", finish="stop")])
        engine, monitor = HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider)), RetryMonitor()
        engine.retry_monitor = monitor
        observer = Observability(sink=monitor)
        attempts = []
        token = monitor.run.set("r")
        try:
            for _ in range(2):
                with observer.scope():
                    async with aclosing(engine.stream_completion(ModelRequest([], {"model": "test/model"}),
                        provider={"max_attempts": 3, "delay_seconds": 0})) as events:
                        async for event in events:
                            self.assertIsNone(monitor.call.get())
                            state = monitor.view("r")
                            if state:
                                attempts.append(state)
                self.assertIsNone(monitor.view("r"))
            self.assertTrue(attempts)
            self.assertTrue(all(s.attempt == 2 and s.retry_at is None for s in attempts))
            self.assertEqual(len(provider.requests), 3)
        finally:
            monitor.run.reset(token)

    async def test_terminal_partial_auth_and_deadline_failures_never_show_retry(self):
        for chunks, policy in (([HTTPFailure(401)], {"max_attempts": 4}),
            ([HTTPFailure(503)], {"max_attempts": 1}),
            ([chunk("partial"), HTTPFailure(503)], {"max_attempts": 4}),
            ([HTTPFailure(503)], {"max_attempts": 4, "delay_seconds": 10, "wall_timeout": 1})):
            monitor = RetryMonitor()
            engine = HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=ScriptedCompletion(chunks)))
            engine.retry_monitor = monitor
            seen = []
            def sink(event):
                monitor(event)
                if monitor.view("r"):
                    seen.append(monitor.view("r"))
            token = monitor.run.set("r")
            try:
                with Observability(sink=sink).scope(), self.assertRaises(Exception):
                    async with aclosing(engine.stream_completion(ModelRequest([], {"model": "test/model"}), provider=policy)) as events:
                        async for _ in events:
                            pass
                self.assertEqual(seen, [])
                self.assertEqual(monitor.states, {})
            finally:
                monitor.run.reset(token)

    async def test_sdk_delegation_does_not_invent_outer_attempts(self):
        monitor = RetryMonitor()
        provider = ScriptedCompletion([HTTPFailure(503)])
        engine = HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))
        engine.retry_monitor = monitor
        token = monitor.run.set("r")
        observer = Observability(sink=monitor)
        try:
            with observer.scope(), self.assertRaises(Exception):
                async with aclosing(engine.stream_completion(
                    ModelRequest([], {"model": "test/model", "num_retries": 2}),
                    provider={"max_attempts": 4})) as events:
                    async for _ in events:
                        self.assertIsNone(monitor.view("r"))
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(observer.snapshot()["providers"]["retries"], 0)
        finally:
            monitor.run.reset(token)

    async def test_parallel_calls_in_one_graph_run_keep_independent_backoffs(self):
        monitor = RetryMonitor()
        async def call_model():
            engine = HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=ScriptedCompletion([HTTPFailure(503)])))
            engine.retry_monitor = monitor
            token = monitor.run.set("graph-run")
            try:
                with Observability(sink=monitor).scope():
                    async with aclosing(engine.stream_completion(ModelRequest([], {"model": "test/model"}),
                        provider={"max_attempts": 3, "delay_seconds": 60})) as events:
                        async for _ in events:
                            pass
            finally:
                monitor.run.reset(token)
        tasks = [asyncio.create_task(call_model()) for _ in range(2)]
        try:
            await eventually(lambda: len(monitor.states) == 2)
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            self.assertIsNotNone(monitor.view("graph-run"))
            self.assertEqual(len(monitor.states), 1)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(monitor.states, {})

    def test_countdown_is_local(self):
        run = RunSummary("r", "running", "loop", 0, True, (), "", RetrySummary(1, 4, 110))
        with patch("hub.widget.feedback.retry_status.monotonic", return_value=104):
            self.assertIn("2번째 시도까지 6초", retry_text(run.retry, Language("ko")))
        with patch("hub.widget.feedback.retry_status.monotonic", return_value=108):
            self.assertIn("2번째 시도까지 2초", retry_text(run.retry, Language("ko")))
        self.assertIn("2/4번째", retry_text(RetrySummary(2, 4), Language("ko")))

    def test_large_approval_is_bounded_but_full_review_preserves_payload(self):
        request = {"title": "Tool approval", "action": {"tool": "file_create", "arguments": {
            "path": "script.py", "content": "PAYLOAD_LINE\n" * 10000, "workspace_id": "w"}}}
        # Exercise the same bounded lines consumed by QuestionComposer.
        summary = "\n".join(review_lines(request, Language("ko")))
        self.assertLess(len(summary), 1000)
        self.assertNotIn("PAYLOAD_LINE", summary)
        self.assertIn("script.py", summary)
        self.assertIn("PAYLOAD_LINE", review_text(request))

    async def test_approval_skips_duplicate_resume_preview(self):
        from unittest.mock import AsyncMock
        review = self.enterContext(patch("hub.backend.execution.stopping.finish_review", new_callable=AsyncMock))
        from llm.core.interactions import InteractionRequest, InteractionOption
        request = InteractionRequest("Approve", (InteractionOption("approve", "Approve", effect="approve"),))
        runtime = HubRuntime(HubConfig(auto_title=False))
        runtime.project = NS(id="p")
        run = NS(arespond=AsyncMock(), ainteractions=AsyncMock(return_value=[]),
                 aget_data=AsyncMock(return_value=NS(agent_id="assistant", engine="loop")))
        session = NS(run=NS(aload=AsyncMock(return_value=run), resume=AsyncMock(), resume_plan=AsyncMock()))
        runtime.sessions = {"s": session}
        self.assertTrue(await runtime.answer_question("p", "s", "r", request.to_dict(), "approve", None))
        session.run.resume_plan.assert_not_awaited()
        session.run.resume.assert_awaited_once_with("r", agent_id="assistant", partial_output=True)
        review.assert_awaited_once_with(session, "r")

    async def test_approval_admission_does_not_wait_for_ui_operation_lock(self):
        from unittest.mock import AsyncMock
        from hub.backend.worker import BackendWorker
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed, worker._failure, worker._snapshot_task = False, None, None
        worker._restarting = False
        worker._commands = set()
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), answer_question=AsyncMock(return_value=True))
        async with worker.runtime.lock:
            async with asyncio.timeout(.5):
                self.assertTrue(await worker._execute("answer_question", ("p", "s", "r", {}, "approve", None)))
        self.assertTrue(worker.runtime.dirty.is_set())

    async def test_durable_ack_precedes_slow_resume_admission(self):
        from unittest.mock import AsyncMock, Mock
        self.enterContext(patch("hub.backend.execution.stopping.finish_review", new_callable=AsyncMock))
        from llm.core.interactions import InteractionRequest, InteractionOption
        request = InteractionRequest("Approve", (InteractionOption("approve", "Approve", effect="approve"),))
        runtime = HubRuntime(HubConfig(auto_title=False))
        runtime.project = NS(id="p")
        acknowledged = Mock()
        entered, release = asyncio.Event(), asyncio.Event()
        run = NS(arespond=AsyncMock(), ainteractions=AsyncMock(return_value=[]),
                 aget_data=AsyncMock(return_value=NS(agent_id="assistant", engine="loop")))
        async def resume(*args, **kwargs):
            run.arespond.assert_awaited_once()
            acknowledged.assert_called_once_with()
            entered.set()
            await release.wait()
        runtime.sessions = {"s": NS(run=NS(aload=AsyncMock(return_value=run), resume=resume))}
        task = asyncio.create_task(runtime.answer_question("p", "s", "r", request.to_dict(), "approve", None,
                                                          acknowledged=acknowledged))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            self.assertFalse(task.done())
            self.assertTrue(runtime.dirty.is_set())
        finally:
            release.set()
            await task
