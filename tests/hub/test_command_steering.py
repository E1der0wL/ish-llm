"""Real native processes remain responsive to durable in-turn instructions."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.execution.request_profile import RequestMode
from hub.backend.execution.tool_exposure import HubToolSelector
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.command_waits import CommandWaits, ConversationalCommands
from hub.config.environment import ENVIRONMENT_OPTION
from llm.components.tools import Tool, ToolExposure, ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelRole
from llm.core.steering import RunInstruction, is_instruction
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.hub.test_shell_environment import snapshot
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm.test_loop import chunk, call


class CommandWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_guidance_follows_native_exposure_not_the_unfiltered_registry(self):
        from hub.backend.execution.request_processing import RequestScopeSession
        from llm.components.processing import CompletionRequest
        from llm.core.model_protocol import ToolDefinition

        session = RequestScopeSession(NS(run=NS(input_message_id='user')), RequestMode.WORK, False)
        for names in ((), ('shell_execute',), tuple(ConversationalCommands.TOOLS)):
            request = CompletionRequest({}, [], 1, tools=tuple(
                ToolDefinition(name, name, {'type': 'object'}) for name in names))
            async for _ in session.prepare(request):
                pass
            guidance = '\n'.join(m.message.text for m in request.messages)
            self.assertEqual(ConversationalCommands.GUIDANCE in guidance,
                             ConversationalCommands.available(names))

    def context(self, run='run'):
        return NS(project=NS(id='project'), session=NS(id='session'), run=NS(id=run))

    def call(self, run='run'):
        return NS(project_id='project', session_id='session', run_id=run)

    async def test_steering_wakes_only_its_run_after_receipt_without_cancelling_process(self):
        waits, entered = CommandWaits(), asyncio.Event()
        async def read(args):
            entered.set()
            self.assertEqual(args, {'process_id': 'native'})
            return {'status': 'running', 'process_id': 'native'}
        with waits.scope(self.context()), patch(
                'hub.backend.tools.command_waits.current_tool_call', return_value=self.call()):
            pending = asyncio.create_task(waits.status(read, {'process_id': 'native'}))
            await entered.wait()
            waits.notify('project', 'session', 'other-run', ['root'])
            waits.notify('project', 'session', 'run', ['child'])
            await asyncio.sleep(0)
            self.assertFalse(pending.done())
            waits.notify('project', 'session', 'run', ['root'])
            self.assertEqual((await asyncio.wait_for(pending, 1))['status'], 'running')
        self.assertEqual(waits.runs, {})

    async def test_no_scope_and_zero_wait_keep_immediate_observation(self):
        waits = CommandWaits()
        read = AsyncMock(return_value={'status': 'running'})
        self.assertEqual(await waits.status(read, {'process_id': 'id'}), {'status': 'running'})
        with waits.scope(self.context()), patch(
                'hub.backend.tools.command_waits.current_tool_call', return_value=self.call()):
            await waits.status(read, {'process_id': 'id', 'wait_seconds': 0})
        self.assertEqual(read.await_count, 2)
        read.assert_awaited_with({'process_id': 'id'})

    async def test_terminal_failure_timeout_and_bounded_wait_preserve_native_status(self):
        waits = CommandWaits()
        with waits.scope(self.context()), patch(
                'hub.backend.tools.command_waits.current_tool_call', return_value=self.call()):
            for status in ('completed', 'failed', 'timed_out', 'cancelled'):
                read = AsyncMock(return_value={'status': status, 'returncode': 7})
                self.assertEqual(await waits.status(read, {'process_id': 'id'}), read.return_value)
                self.assertEqual(read.await_count, 1)
            read = AsyncMock(return_value={'status': 'running'})
            result = await asyncio.wait_for(waits.status(read, {'process_id': 'id', 'wait_seconds': .02}), 1)
            self.assertEqual(result, {'status': 'running'})

    async def test_cancel_wait_propagates_and_releases_run_scope(self):
        waits, entered = CommandWaits(), asyncio.Event()
        async def read(args):
            entered.set()
            return {'status': 'running'}
        async def observe():
            with waits.scope(self.context()), patch(
                    'hub.backend.tools.command_waits.current_tool_call', return_value=self.call()):
                await waits.status(read, {'process_id': 'id'})
        pending = asyncio.create_task(observe())
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(waits.runs)

    async def test_invalid_wait_does_not_read_or_start_anything(self):
        read = AsyncMock()
        for value in (-1, float('inf'), float('nan'), True, '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await CommandWaits().status(read, {'process_id': 'id', 'wait_seconds': value})
        read.assert_not_called()

    async def test_exposure_uses_complete_selected_suite_and_keeps_greeting_small(self):
        names = (*sorted(ConversationalCommands.TOOLS), 'shell_execute', 'ask_user')
        tools = ToolRegistry(tuple(Tool(name, name, {'type': 'object'}, AsyncMock(),
            contract=TEST_CONTRACT) for name in names))
        request = ToolExposure(1, (ModelMessage(ModelRole.USER, 'Explain disk usage'),), names, False)
        selector = HubToolSelector(tools, RequestMode.LOOKUP)
        selected = await selector.select_tools(request)
        self.assertTrue(ConversationalCommands.TOOLS <= set(selected))
        self.assertNotIn('shell_execute', selected)
        narrower = replace(request, available=tuple(n for n in names if n != 'process_cancel'))
        selected = await selector.select_tools(narrower)
        self.assertIn('shell_execute', selected)
        self.assertNotIn('process_cancel', selected)
        greeting = replace(request, messages=(ModelMessage(ModelRole.USER, 'hello'),))
        self.assertEqual(await HubToolSelector(tools, RequestMode.CONVERSATION).select_tools(greeting), ('ask_user',))


class CommandSteeringIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_runs_once_and_steering_is_answered_before_exit(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root, requests, results = Path(directory), [], []
        work = root / 'work'
        work.mkdir()
        script = (
            'from pathlib import Path; import time, os\n'
            'Path("count").open("a").write("once\\n")\n'
            'print(os.environ["HUB_SHELL_TEST"], flush=True)\n'
            'while not Path("release").exists(): time.sleep(.02)\n'
            'print("finished", flush=True)\n'
        )
        def provider(**request):
            requests.append(request)
            index = len(requests)
            if index == 1:
                yield chunk(calls=[call(json.dumps({'argv': [sys.executable, '-c', script],
                    'timeout_seconds': 60}), name='process_start', call_id='start')], finish='tool_calls')
                return
            value = json.loads(next(m['content'] for m in reversed(request['messages']) if m['role'] == 'tool'))
            results.append(value)
            if index == 3:
                self.assertIn('Tell me what is happening', str(request['messages']))
                self.assertEqual(value['status'], 'running')
                self.assertFalse((work / 'release').exists())
                yield chunk('The command is still running; I received your instruction.')
            if index < 4:
                yield chunk(calls=[call(json.dumps({'process_id': value['process_id']}),
                    name='process_status', call_id=f'wait-{index}')], finish='tool_calls')
            else:
                self.assertEqual(value['status'], 'completed')
                yield chunk('The same command completed.', finish='stop')

        runtime = HubRuntime(HubConfig(root / 'storage', auto_title=False, model='test/steering',
            agent_definition={'engine': 'loop', 'tools': [*sorted(ConversationalCommands.TOOLS), 'shell_execute']},
            engine_factories={'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        runtime.instruction_routing.assess = AsyncMock(return_value='steer')
        sid = await runtime.new_session()
        entered, answered = asyncio.Event(), asyncio.Event()
        original = runtime.builtin_tools.command_waits.status
        count = 0
        async def status(read, args):
            nonlocal count
            count += 1
            (entered if count == 1 else answered).set()
            return await original(read, args)
        with patch.object(runtime.builtin_tools.command_waits, 'status', side_effect=status):
            await runtime.submit(sid, 'Run the command and inspect its result.', request_context={
                ENVIRONMENT_OPTION: snapshot(work, 'captured').to_dict()})
            await asyncio.wait_for(runtime.sessions[sid].run.wait_idle(), 30)
            handle, = await runtime.sessions[sid].run.alist()
            self.assertEqual((await handle.aget_data()).status, 'paused')
            self.assertFalse((work / 'count').exists())
            approval, = await handle.ainteractions(pending_only=True)
            await handle.arespond(approval.respond('approve'))
            resumed = await runtime.sessions[sid].run.resume(handle.id, agent_id='assistant')
            await asyncio.wait_for(entered.wait(), 30)
            status_value = await runtime.sessions[sid].run.astatus(queued_limit=0)
            run_id = status_value.active_runs[0].run_id
            instruction = await runtime.steer(sid, run_id, 'Tell me what is happening, then keep waiting.', ['root'])
            await asyncio.wait_for(answered.wait(), 10)
            live = await runtime.snapshot()
            visible = '\n'.join(m.text + (m.draft.text if m.draft is not None else '')
                                for m in live.messages)
            self.assertIn('I received your instruction.', visible)
            self.assertTrue(any(i.id == instruction and i.status == 'applied'
                                for m in live.messages for i in m.instructions))
            from hub.locales.keys import MessageKey
            details = [pair for message in live.messages for item in message.trace for pair in item.details]
            self.assertTrue(any(key == MessageKey.CARD_INPUT and 'release' in value
                                for key, value in details), details)
            self.assertEqual((work / 'count').read_text(), 'once\n')
            self.assertFalse((work / 'release').exists())
            (work / 'release').touch()
            run, = await asyncio.wait_for(resumed.wait(), 30)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(requests), 4)
        self.assertEqual((work / 'count').read_text(), 'once\n')
        self.assertIn('captured', results[-1]['stdout'])
        self.assertIn('finished', results[-1]['stdout'])
        conversation = await runtime.sessions[sid].aconversation()
        receipt = next(RunInstruction.from_message(m) for m in conversation if m.id == instruction and is_instruction(m))
        self.assertEqual(receipt.status, 'applied')
        observed = await runtime.snapshot()
        request_bubble = next(m for m in observed.messages if m.instructions)
        self.assertEqual(request_bubble.instructions[0].id, instruction)
        self.assertEqual(request_bubble.instructions[0].status, 'applied')
        self.assertFalse(any(m.id == instruction for m in observed.messages))
        # Completed responses retain the existing collapsed thought presentation;
        # the same final Step projection still carries bounded command results.
        details = [pair for step in observed.run.steps for pair in step.details]
        self.assertTrue(any(key == MessageKey.TOOL_RESULT and 'finished' in value
                            for key, value in details), details)
        self.assertEqual(len(await runtime.sessions[sid].run.alist()), 2)  # Approval resume, not steering.
        self.assertFalse(runtime.builtin_tools.command_waits.runs)
        for request in requests:
            exposed = {tool['function']['name'] for tool in request.get('tools', ())}
            self.assertNotIn('shell_execute', exposed)
            self.assertTrue(ConversationalCommands.TOOLS <= exposed)
