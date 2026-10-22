"""One retry projection for providers and Tools, including live header feedback."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from contextlib import aclosing
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hub.backend.execution.environment import EnvironmentEngine
from hub.backend.execution.retries import CompletionWatch, HubLoopEngine, RetryMonitor
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage
from hub.widget.feedback.retry_status import retry_text
from hub.widget.transcript.conversation import render_messages
from llm.engines.base import EngineEvent, EngineEventType as E
from llm.policies.retry import RetryDecision
from llm.services.runtime.tools import ToolExecutionError
from tests.hub.support import eventually
from tests.hub.test_tool_retry import ExampleTools
from tests.llm.test_loop import ScriptedCompletion, call, chunk


def context(run_id="r", maximum=2):
    return NS(run=NS(id=run_id, metadata={"policies": {
        "tool_retry": {"max_retries": maximum, "delay_seconds": 5}}}))


def start(monitor, ctx, step="s"):
    monitor.observe_tool(ctx, EngineEvent(E.STEP_STARTED, step_id=step, kind="tool", name="convert"))


def update(monitor, ctx, phase, attempt=1, step="s"):
    monitor.observe_tool(ctx, EngineEvent(E.STEP_UPDATED, step_id=step,
        metadata={"phase": phase, "retry_attempt": attempt}))


class RetryObservationTests(unittest.IsolatedAsyncioTestCase):
    def test_tool_counts_failed_attempts_and_actual_execution_separately(self):
        monitor, ctx = RetryMonitor(), context()
        start(monitor, ctx)
        self.assertIsNone(monitor.view("r"))
        update(monitor, ctx, "repairing")
        text = retry_text(monitor.view("r"), Language("ko"))
        self.assertIn("2/3번째 시도", text)
        self.assertIn("Tool convert", text)
        self.assertIn("수정안 생성 중", text)
        self.assertNotIn("초", text)
        with patch("hub.backend.execution.retries.monotonic", return_value=100):
            update(monitor, ctx, "retrying")
        for now, left in ((101, 4), (103, 2)):
            with patch("hub.widget.feedback.retry_status.monotonic", return_value=now):
                self.assertIn(f"2번째 시도까지 {left}초", retry_text(monitor.view("r"), Language("ko")))
        # Same-argument retries don't emit another 'executing' Step event.
        monitor({"kind": "tools", "status": "executions", "run_id": "r", "step_id": "s"})
        active = monitor.view("r")
        self.assertEqual((active.attempt, active.maximum, active.retry_at, active.phase), (2, 3, None, "active"))
        self.assertIn("Retrying · Tool convert · attempt 2/3", retry_text(active, Language("en")))
        update(monitor, ctx, "repairing", 2)
        self.assertIn("3/3번째", retry_text(monitor.view("r"), Language("ko")))

    def test_terminal_tools_do_not_erase_other_calls_or_sessions(self):
        for terminal in (E.STEP_COMPLETED, E.STEP_FAILED, E.STEP_INTERRUPTED, E.STEP_CANCELLED):
            with self.subTest(terminal=terminal):
                monitor, ctx, other = RetryMonitor(), context(), context("other")
                watch = CompletionWatch("r", {"max_attempts": 4, "delay_seconds": 10}, attempt=1)
                token = monitor.call.set(watch)
                try:
                    monitor({"kind": "providers", "status": "retries", "name": "completion"})
                finally:
                    monitor.call.reset(token)
                provider = monitor.view("r")
                start(monitor, ctx)
                start(monitor, other)
                update(monitor, ctx, "retrying")
                update(monitor, other, "retrying")
                self.assertEqual(monitor.view("r").kind, "tool")
                monitor.observe_tool(ctx, EngineEvent(terminal, step_id="s"))
                self.assertEqual(monitor.view("r"), provider)
                self.assertEqual(monitor.view("other").kind, "tool")
                monitor.clear_tools("other")
                self.assertIsNone(monitor.view("other"))
                self.assertEqual(monitor.tools, {})

    async def test_event_stream_closure_clears_retry_and_waits_for_consumption(self):
        monitor, ctx = RetryMonitor(), context()
        class Engine:
            async def execute(self, context):
                yield EngineEvent(E.STEP_STARTED, step_id="s", kind="tool", name="convert")
                yield EngineEvent(E.STEP_UPDATED, step_id="s", metadata={"phase": "retrying", "retry_attempt": 1})
                yield EngineEvent(E.TEXT_DELTA)
        wrapped = EnvironmentEngine(Engine(), None, retry_monitor=monitor)
        async with aclosing(wrapped.execute(ctx)) as events:
            await anext(events)
            await anext(events)
            self.assertIsNone(monitor.view("r"))
            await anext(events)
            self.assertIsNotNone(monitor.view("r"))
        self.assertEqual(monitor.states, {})
        self.assertEqual(monitor.tools, {})

    async def test_live_tool_repair_and_execution_reach_snapshot_and_header(self):
        choosing, release_choice = asyncio.Event(), asyncio.Event()
        executing, release_tool = asyncio.Event(), asyncio.Event()
        async def chooser(request):
            choosing.set()
            await release_choice.wait()
            return RetryDecision({"format": "new"})
        async def handler(args):
            if args["format"] == "old":
                raise ToolExecutionError("Use new format", effect="none", recoverable=True)
            executing.set()
            await release_tool.wait()
            return "converted"
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")],
            [chunk("Done", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))},
                component_factories=[lambda: ExampleTools(handler)], tool_retry_chooser=chooser,
                project_config={"policies": {"tool_retry": {"max_retries": 2, "delay_seconds": .01}}}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                request = await runtime.sessions[sid].run.submit("Convert", recipients=["assistant"])
                await asyncio.wait_for(choosing.wait(), 10)
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.run.retry.phase, "repairing")
                release_choice.set()
                await asyncio.wait_for(executing.wait(), 10)
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.run.retry.attempt, 2)
                self.assertEqual(snapshot.run.retry.phase, "active")
                message = ChatMessage("assistant", "body must not be rendered", status="streaming",
                                      activity=retry_text(snapshot.run.retry, Language("ko")))
                rows = render_messages((message,), 120, HubTheme(), Language("ko"), section="header")
                header = "".join(text for row in rows for _, text in row)
                self.assertIn("재시도 중 · Tool convert · 2/3번째 시도", header)
                self.assertNotIn(message.text, header)
                release_tool.set()
                result = (await request.wait())[0]
                self.assertEqual(str(result.data.status), "completed", result.data.error)
                self.assertIsNone((await runtime.snapshot()).run.retry)
                monitor = runtime.backend.services.observability_sink
                self.assertEqual(monitor.states, {})
                self.assertEqual(monitor.tools, {})
            finally:
                release_choice.set()
                release_tool.set()
                await runtime.close()

    async def test_interrupt_clears_unchanged_argument_tool_backoff(self):
        async def handler(args):
            raise ToolExecutionError("Busy", effect="none", retryable=True)
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"format":"old","count":1}', name="convert")], finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: HubLoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))},
                component_factories=[lambda: ExampleTools(handler)],
                project_config={"policies": {"tool_retry": {"max_retries": 3, "delay_seconds": 30}}}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                request = await runtime.sessions[sid].run.submit("Convert", recipients=["assistant"])
                monitor = runtime.backend.services.observability_sink
                await eventually(lambda: bool(monitor.states))
                state = (await runtime.snapshot()).run.retry
                self.assertEqual((state.kind, state.attempt, state.maximum), ("tool", 1, 4))
                self.assertIsNotNone(state.retry_at)
                await runtime.sessions[sid].run.interrupt()
                await request.wait()
                self.assertEqual(monitor.states, {})
                self.assertEqual(monitor.tools, {})
                self.assertIsNone((await runtime.snapshot()).run.retry)
            finally:
                await runtime.close()
