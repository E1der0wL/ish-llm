"""Repeated cancellation cannot abandon cleanup at shared execution boundaries."""

import asyncio
import unittest
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from llm.engines.steering import InvocationControl
from llm.providers.mcp_session import MCPSession
from llm.services.infrastructure.cancellation import cancel_tasks
from llm.services.runtime.output import consume_events
from llm.core.policies import OutputBuffer
from llm.engines.base import EngineContext, EngineEvent, EngineEventType
from llm.core.results import EngineDelta
from llm.engines.agents import AgentDelegation
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph
from llm.components.tools.builtin.workspaces import TestWorkspaces
from llm.components.tools.builtin.processes import ProcessTools, ProcessSession, ProcessStatus
from llm.components.vision.backends import TesseractBackend


class CancellationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def assert_repeated_cancel_drains(self, consume, entered, closing, release, closed):
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.wait_for(closing.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            self.assertTrue(closed.is_set())
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_output_cancel_drains_source_and_flushes_accepted_delta(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        saved = []
        async def source():
            try:
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(text='retained'))
                # The consumer can accept the queued delta before cancellation.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                entered.set()
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
                closed.set()
        async def handle(events):
            saved.extend(events)
        async def consume():
            await consume_events(source(), OutputBuffer(batch_size=10, max_delay=60, max_chars=100), handle)
        await self.assert_repeated_cancel_drains(consume, entered, closing, release, closed)
        self.assertEqual([item.delta.text for item in saved], ['retained'])

    async def test_agent_event_bridge_drains_child_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        class Runtime:
            revision = 'test'
            def prepare_context(self, definition, context):
                return context
            def validate(self, definition, context):
                pass
            def binding(self, definition, context):
                return {}
            async def __call__(self, node):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closing.set()
                    await release.wait()
                    closed.set()
        context = EngineContext(project=None, session=None, run=None, messages=())
        async def consume():
            async for _ in AgentDelegation(Runtime()).execute_events(context,
                    {'agent_id': 'child', 'input': {}}, request_key='tool',
                    checkpoint_name='loop', step_id='step', result={}):
                pass
        await self.assert_repeated_cancel_drains(consume, entered, closing, release, closed)

    async def test_graph_event_bridge_drains_workflow_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        async def execute(runtime, *args):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
                closed.set()
        engine = GraphEngine(handlers={}).for_agent(
            {'engine': 'graph', 'engine_options': {'workflow': 'flow'}})
        graph = WorkflowGraph(entry='end').node('end', 'end').to_dict()
        context = EngineContext(project=None, session=None,
            run=SimpleNamespace(input_message_id='input'),
            messages=(SimpleNamespace(id='input', content='request'),))
        async def consume():
            async for _ in engine._execute(context):
                pass
        with patch.object(engine, '_prepare', return_value=(graph, {})), \
                patch.object(engine, '_binding', return_value={}), \
                patch('llm.engines.graph.engine._WorkflowRuntime.run', execute):
            await self.assert_repeated_cancel_drains(consume, entered, closing, release, closed)

    async def test_concurrent_workspace_close_preserves_active_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source'
            source.mkdir()
            workspaces = TestWorkspaces(Path(root) / 'workspaces', source_root=source, allow_network=False)
            async def operation():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closing.set()
                    await release.wait()
                    closed.set()
            task = asyncio.create_task(operation())
            workspaces._active.add(task)
            closers = []
            try:
                await asyncio.wait_for(entered.wait(), 5)
                closers.append(asyncio.create_task(workspaces.close()))
                await asyncio.wait_for(closing.wait(), 5)
                closers.append(asyncio.create_task(workspaces.close()))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                await asyncio.wait_for(asyncio.gather(*closers), 5)
                self.assertTrue(closed.is_set())
            finally:
                release.set()
                await asyncio.gather(task, *closers, return_exceptions=True)

    async def test_concurrent_process_stop_does_not_recancel_monitor_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        async def monitor():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
                closed.set()
        task = asyncio.create_task(monitor())
        item = ProcessSession(None, [], '', 'test', task=task, status=ProcessStatus.CANCELLED)
        tools = ProcessTools(None)
        tools.sessions['owned'] = item
        stops = []
        try:
            await asyncio.wait_for(entered.wait(), 5)
            stops.append(asyncio.create_task(tools._stop(item)))
            await asyncio.wait_for(closing.wait(), 5)
            stops.append(asyncio.create_task(tools._stop(item)))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            await asyncio.wait_for(asyncio.gather(*stops), 5)
            self.assertTrue(closed.is_set())
        finally:
            release.set()
            await asyncio.gather(task, *stops, return_exceptions=True)
            await tools.close()

    async def test_process_close_during_close_waits_for_shared_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        async def monitor():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                closing.set()
                await release.wait()
                closed.set()
        task = asyncio.create_task(monitor())
        tools = ProcessTools(None)
        tools.sessions['owned'] = ProcessSession(None, [], '', 'test', task=task, status=ProcessStatus.CANCELLED)
        closers = []
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closers.append(asyncio.create_task(tools.close()))
            await asyncio.wait_for(closing.wait(), 5)
            closers.append(asyncio.create_task(tools.close()))
            await asyncio.sleep(0)
            self.assertFalse(closers[1].done())
            closers[0].cancel()
            await asyncio.sleep(0)
            closers[0].cancel()
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*closers, return_exceptions=True), 5)
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertIsNone(results[1])
            self.assertTrue(closed.is_set())
        finally:
            release.set()
            await asyncio.gather(task, *closers, return_exceptions=True)

    async def test_process_close_error_does_not_close_other_live_captures(self):
        entered, release = asyncio.Event(), asyncio.Event()
        captures = []
        def item(name):
            capture = SimpleNamespace(close=lambda: captures.append(name))
            return SimpleNamespace(name=name, stdout=capture, stderr=capture)
        tools = ProcessTools(None)
        tools.sessions = {'failed': item('failed'), 'pending': item('pending')}
        async def stop(session):
            if session.name == 'failed':
                raise OSError('termination failed')
            entered.set()
            await release.wait()
        with patch.object(tools, '_stop', stop):
            task = asyncio.create_task(tools.close())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await asyncio.sleep(0)
                self.assertEqual(captures, [])
                release.set()
                with self.assertRaisesRegex(OSError, 'termination failed'):
                    await asyncio.wait_for(task, 5)
                self.assertCountEqual(captures, ['failed', 'failed', 'pending', 'pending'])
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_ocr_pipe_failure_waits_for_process_termination(self):
        entered, release, closed = (asyncio.Event() for _ in range(3))
        process = SimpleNamespace(communicate=AsyncMock(side_effect=OSError('pipe failed')))
        async def terminate(process):
            entered.set()
            await release.wait()
            closed.set()
        with patch('llm.components.vision.backends.asyncio.create_subprocess_exec', AsyncMock(return_value=process)), \
                patch('llm.components.vision.backends.kill_process_tree', terminate):
            task = asyncio.create_task(TesseractBackend().recognize(b'image', options={}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaisesRegex(OSError, 'pipe failed'):
                    await asyncio.wait_for(task, 5)
                self.assertTrue(closed.is_set())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stream_second_cancel_preserves_generator_cleanup(self):
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        async def stream():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield 'unreachable'
            finally:
                closing.set()
                await release.wait()
                closed.set()
        async def consume():
            async for _ in InvocationControl().stream(stream()):
                self.fail('cancelled stream published a result')
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.wait_for(closing.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            self.assertTrue(closed.is_set())
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_concurrent_mcp_close_does_not_cancel_cleanup_twice(self):
        client = MCPSession(None, None, SimpleNamespace(model_dump=lambda **_: {}), connection_id='test')
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        async def operation():
            async with client.operation():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                finally:
                    closing.set()
                    await release.wait()
                    closed.set()
        task = asyncio.create_task(operation())
        closers = []
        try:
            await asyncio.wait_for(entered.wait(), 5)
            closers.append(asyncio.create_task(client.close()))
            await asyncio.wait_for(closing.wait(), 5)
            closers.append(asyncio.create_task(client.close()))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(*closers), 5)
            self.assertTrue(closed.is_set())
            self.assertEqual(client.calls, set())
            self.assertFalse(client.active)
        finally:
            release.set()
            await asyncio.gather(task, *closers, return_exceptions=True)

    async def test_collection_rejects_owning_task_before_cancelling_children(self):
        child = asyncio.create_task(asyncio.Event().wait())
        try:
            with self.assertRaisesRegex(ValueError, 'itself'):
                await cancel_tasks([child, asyncio.current_task()])
            self.assertFalse(child.cancelling())
        finally:
            await cancel_tasks([child])
