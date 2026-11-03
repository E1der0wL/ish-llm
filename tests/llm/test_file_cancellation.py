"""File preparation cancellation and publication races through actual Tool paths."""

import asyncio
from contextlib import contextmanager, AsyncExitStack
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from llm.components.tools import ToolRegistry
from llm.components.tools.builtin import BuiltinTools, TestWorkspaces
from llm.components.tools.builtin.file_execution import FileExecution, FileReader
from llm.components.tools.builtin import files as file_module
from llm.engines.steering import InvocationControl, InvocationRedirected
from llm.services.runtime.tools import ToolCall
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import chunk, call
from tests.llm import test_loop_steering as loop_tests


class FileCancellationTests(unittest.IsolatedAsyncioTestCase):
    setup_backend = loop_tests.LoopSteeringTests.setup_backend

    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.tools = BuiltinTools(self.root)
        self.addAsyncCleanup(self.tools.close)

    async def invoke(self, name, **args):
        tool, arguments = self.tools.registry.prepare(name, args)
        return await tool.handler(arguments)

    @contextmanager
    def gate(self, target, attribute):
        """Block one genuine I/O boundary until cancellation has reached its worker."""
        loop, entered, cancelled = asyncio.get_running_loop(), asyncio.Event(), asyncio.Event()
        release = threading.Event()
        calls = []
        original = getattr(target, attribute)
        cancel = FileExecution.cancel
        def blocked(*args, **kwargs):
            if target is file_module.os and attribute == 'link' and 'src_dir_fd' not in kwargs:
                return original(*args, **kwargs)  # Leave domain storage transactions running.
            calls.append(1)
            if len(calls) == 1:
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(20):
                    raise TimeoutError('test gate not released')
            return original(*args, **kwargs)
        def stop(execution):
            cancel(execution)
            cancelled.set()
        try:
            with patch.object(target, attribute, blocked), patch.object(FileExecution, 'cancel', stop):
                yield entered, cancelled, release, calls
        finally:
            release.set()

    async def test_read_search_and_patch_preparation_stop_between_blocks(self):
        path = self.root / 'large.txt'
        path.write_text('needle\n' * 50000)
        original = path.read_bytes()
        digest = self.tools.files.digest(original)
        cases = [('file_read', {'path': path.name}),
                 ('file_read', {'path': path.name, 'offset': 2, 'max_chars': 5}),
                 ('file_search', {'path': '.', 'query': 'needle', 'case_sensitive': True, 'recursive': True}),
                 ('file_patch', {'path': path.name, 'expected_sha256': digest,
                                 'old_text': 'needle', 'new_text': 'other'})]
        for operation, arguments in cases:
            with self.subTest(operation=operation), self.gate(FileReader, 'read') as (entered, stopped, release, calls):
                control = InvocationControl()
                task = asyncio.create_task(control.call(lambda: self.invoke(operation, **arguments)))
                await asyncio.wait_for(entered.wait(), 5)
                control.request('change')
                await asyncio.wait_for(stopped.wait(), 5)
                release.set()
                with self.assertRaises(InvocationRedirected):
                    await asyncio.wait_for(task, 5)
                self.assertEqual(len(calls), 1, 'cancelled preparation continued reading')
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(self.root.glob('.llm-write-*')), [])

    async def test_cancel_staged_write_removes_temporary_file(self):
        # Temp-file fsync precedes publication. Cancelling here must never link it.
        with self.gate(file_module.os, 'fsync') as (entered, stopped, release, _):
            control = InvocationControl()
            task = asyncio.create_task(control.call(lambda: self.invoke('file_create', path='new', content='x' * 150000)))
            await asyncio.wait_for(entered.wait(), 5)
            control.request('change')
            await asyncio.wait_for(stopped.wait(), 5)
            release.set()
            with self.assertRaises(InvocationRedirected):
                await asyncio.wait_for(task, 5)
        self.assertFalse((self.root / 'new').exists())
        self.assertEqual(list(self.root.glob('.llm-write-*')), [])

    async def test_committing_write_preserves_result_even_with_repeated_cancel(self):
        with self.gate(file_module.os, 'link') as (entered, stopped, release, calls):
            control = InvocationControl()
            task = asyncio.create_task(control.call(lambda: self.invoke('file_create', path='new', content='saved')))
            await asyncio.wait_for(entered.wait(), 5)
            control.request('change')
            await asyncio.wait_for(stopped.wait(), 5)
            control.request('another change')
            self.assertFalse(task.done())
            release.set()
            result = await asyncio.wait_for(task, 5)
            self.assertEqual(result['sha256'], self.tools.files.digest(b'saved'))
            self.assertEqual(len(calls), 1)
        self.assertEqual((self.root / 'new').read_text(), 'saved')
        self.assertEqual(list(self.root.glob('.llm-write-*')), [])

    async def test_cancelled_waiter_does_not_wait_for_file_lock_release(self):
        locked, release = threading.Event(), threading.Event()
        def hold():
            with self.tools.files.lock:
                locked.set()
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(await asyncio.to_thread(locked.wait, 5))
            task = asyncio.create_task(self.invoke('file_list', path='.', recursive=True))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            self.assertTrue(holder.is_alive())
        finally:
            release.set()
            await asyncio.to_thread(holder.join)

    async def test_workspace_copy_cancellation_cleans_preparation_and_allows_reopen(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'large').write_bytes(b'x' * 300000)
        storage = self.root / 'spaces'
        call_context = ToolCall('test', {}, 'step', project_id='project', contract=TEST_CONTRACT.to_dict())
        async with AsyncExitStack() as stack:
            spaces = TestWorkspaces(storage, source_root=source, allow_network=False)
            stack.push_async_callback(spaces.close)
            with patch('llm.components.tools.builtin.workspaces.current_tool_call', return_value=call_context):
                with self.gate(FileReader, 'read') as (entered, stopped, release, calls):
                    task = asyncio.create_task(spaces.create({'source_paths': ['large']}))
                    await asyncio.wait_for(entered.wait(), 5)
                    task.cancel()
                    await asyncio.wait_for(stopped.wait(), 5)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                    self.assertEqual(len(calls), 1)
                self.assertEqual(spaces.repository.list('project').to_dict()['workspaces'], [])
                record = await spaces.create({'source_paths': ['large']})
        async with AsyncExitStack() as stack:
            reopened = TestWorkspaces(storage, source_root=source, allow_network=False)
            stack.push_async_callback(reopened.close)
            with patch('llm.components.tools.builtin.workspaces.current_tool_call', return_value=call_context):
                result = await reopened.file_call('file_read', {'workspace_id': record['workspace_id'],
                                                               'path': 'large', 'max_chars': 5})
                self.assertEqual(result['text'], 'xxxxx')
                await reopened.delete({'workspace_id': record['workspace_id']})
        self.assertEqual((source / 'large').stat().st_size, 300000)

    async def test_real_approval_redirect_completed_write_resume_never_replays(self):
        tool = self.tools.registry.get('file_create')
        component = RuntimeTools(ToolRegistry([tool]))
        model = loop_tests.Model([chunk(calls=[call(json.dumps({'path': 'saved', 'content': 'once'}),
                                        name='file_create')], finish='tool_calls')],
                      [ConnectionError('fixture model failure after write')],
                      [chunk('done', finish='stop')], gate=False)
        _, session, initial, paused = await self.setup_backend(model, components=[component])
        paused = (await initial.wait(timeout=10))[0]
        self.assertEqual(paused.data.status, 'paused')
        self.assertFalse((self.root / 'saved').exists())
        approval, = await paused.ainteractions()
        await paused.arespond(approval.respond('approve'))
        with self.gate(file_module.os, 'link') as (entered, stopped, release, calls):
            request = await session.run.resume(paused.id, agent_id='worker')
            await asyncio.wait_for(entered.wait(), 10)
            run, = await request.aget_runs()
            await session.run.redirect(run.id, 'keep completed changes; new plan')
            await asyncio.wait_for(stopped.wait(), 10)
            release.set()
            failed = (await request.wait(timeout=10))[0]
            self.assertEqual(failed.data.status, 'failed')
            resumed = (await (await session.run.resume(failed.id, agent_id='worker')).wait(timeout=10))[0]
            self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
            self.assertEqual(len(calls), 1)
        self.assertEqual((self.root / 'saved').read_text(), 'once')
        tool_results = [value for value in model.requests[-1]['messages'] if value['role'] == 'tool']
        self.assertEqual(len(tool_results), 1)
        self.assertIn(self.tools.files.digest(b'once'), tool_results[0]['content'])

    async def test_parent_interrupt_during_commit_remains_interrupted(self):
        tool = self.tools.registry.get('file_create')
        component = RuntimeTools(ToolRegistry([tool]))
        model = loop_tests.Model([chunk(calls=[call('{"path":"saved","content":"once"}',
                                                   name='file_create')], finish='tool_calls')],
                      [chunk('must not continue', finish='stop')], gate=False)
        _, session, initial, _ = await self.setup_backend(model, components=[component])
        paused = (await initial.wait(timeout=10))[0]
        approval, = await paused.ainteractions()
        await paused.arespond(approval.respond('approve'))
        with self.gate(file_module.os, 'link') as (entered, stopped, release, calls):
            request = await session.run.resume(paused.id, agent_id='worker')
            await asyncio.wait_for(entered.wait(), 10)
            interruption = asyncio.create_task(session.run.interrupt())
            await asyncio.wait_for(stopped.wait(), 10)
            release.set()
            await asyncio.wait_for(interruption, 10)
            result = (await request.wait(timeout=10))[0]
            self.assertEqual(len(calls), 1)
        self.assertEqual(result.data.status, 'interrupted')
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((self.root / 'saved').read_text(), 'once')
        with self.assertRaises(ValueError):
            await session.run.resume(result.id, agent_id='worker')
