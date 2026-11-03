"""Explicit direction changes at real Run/Step/checkpoint boundaries."""

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from llm.components.tools import Tool, ToolRegistry
from llm.core.tool_contracts import ToolEffect
from llm.engines.steering import InvocationControl, InvocationRedirected
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import chunk, call
from tests.llm import test_loop_steering as loop_tests
from tests.llm import test_engine_steering as graph_tests
from tests.llm import test_instruction_reservations as reservation_tests


class BlockedModel:
    def __init__(self, *, fail_second=False, first=None):
        self.requests = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.fail_second = fail_second
        self.first = first or chunk('obsolete', finish='stop')

    def __call__(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        index = len(self.requests)
        if index == 1:
            self.entered.set()
            if not self.release.wait(15):
                raise TimeoutError('fixture gate')
            yield self.first
        elif index == 2 and self.fail_second:
            raise ConnectionError('fixture failure after redirect')
        else:
            yield chunk('revised', finish='stop')


class DirectionTests(unittest.IsolatedAsyncioTestCase):
    setup_backend = loop_tests.LoopSteeringTests.setup_backend

    async def test_failed_redirect_persistence_never_cancels_model(self):
        model = BlockedModel()
        app, session, request, run = await self.setup_backend(model)
        store = app.project_manager.sessions.conversations(session.data)
        create = store.create
        def fail(*args, **kwargs):
            create(*args, **kwargs)
            raise OSError('fixture failed admission')
        with patch.object(store, 'create', side_effect=fail):
            with self.assertRaises(OSError):
                await session.run.redirect(run.id, 'not accepted')
        self.assertEqual(await run.ainstructions(), [])
        self.assertEqual(len(model.requests), 1)
        model.release.set()
        self.assertEqual((await request.wait(timeout=10))[0].data.status, 'completed')

    async def test_selected_before_applied_cannot_be_withdrawn(self):
        from llm.engines.loop import LoopEngine
        gate, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        original = LoopEngine.apply_instructions
        async def apply(context, **kwargs):
            if kwargs.get('boundary') == 'steering:2':
                gate.set()
                await release.wait()
            async for event in original(context, **kwargs):
                yield event
        model = BlockedModel()
        _, session, request, run = await self.setup_backend(model)
        value = await session.run.steer(run.id, 'selected')
        with patch.object(LoopEngine, 'apply_instructions', side_effect=apply):
            model.release.set()
            await asyncio.wait_for(gate.wait(), 10)
            with self.assertRaisesRegex(ValueError, 'selected'):
                await session.run.withdraw_instruction(run.id, value.id)
            with self.assertRaisesRegex(ValueError, 'selected'):
                await session.run.replace_instruction(run.id, value.id, 'replacement')
            release.set()
            self.assertEqual((await request.wait(timeout=10))[0].data.status, 'completed')

    async def test_redirect_keeps_approval_and_explicit_resume(self):
        effects = []
        async def act(args):
            effects.append('executed')
        contract = replace(TEST_CONTRACT, effect=ToolEffect.EXTERNAL, approval_required=True)
        component = RuntimeTools(ToolRegistry([Tool('act', 'test', {'type': 'object'}, act, contract=contract)]))
        class ApprovalModel(BlockedModel):
            def __call__(self, **kwargs):
                if len(self.requests) == 1:
                    self.requests.append(deepcopy(kwargs))
                    yield chunk(calls=[call('{}', name='act')], finish='tool_calls')
                else:
                    yield from super().__call__(**kwargs)
        model = ApprovalModel()
        _, session, request, run = await self.setup_backend(model, components=[component])
        await session.run.redirect(run.id, 'use the tool with approval')
        paused = (await request.wait(timeout=10))[0]
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        with self.assertRaises(ValueError):
            await session.run.redirect(paused.id, 'cannot bypass pause')
        interaction, = await paused.ainteractions()
        await paused.arespond(interaction.respond('approve'))
        self.assertEqual(effects, [])
        model.release.set()
        resumed = (await (await session.run.resume(paused.id, agent_id='worker')).wait(timeout=10))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, ['executed'])

    async def test_redirect_without_waiting_for_old_sdk_and_deduplicate(self):
        model = BlockedModel()
        _, session, request, run = await self.setup_backend(model)
        self.assertTrue((await run.ainstruction_targets())[0].redirectable)
        value = await session.run.redirect(run.id, 'new direction', request_key='redirect-1')
        repeated = await session.run.redirect(run.id, 'new direction', request_key='redirect-1')
        self.assertEqual(value.id, repeated.id)
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual(result.id, run.id)
        self.assertFalse(model.release.is_set())
        self.assertEqual(len(model.requests), 2)
        self.assertEqual([m['content'] for m in model.requests[-1]['messages']], ['original', 'new direction'])
        self.assertEqual((await run.ainstructions())[0].status, 'applied')
        steps = await run.steps.alist()
        self.assertIn('cancelled', [s.status for s in steps])

    async def test_redirected_iteration_is_not_replayed_on_resume(self):
        model = BlockedModel(fail_second=True)
        _, session, request, run = await self.setup_backend(model)
        await session.run.redirect(run.id, 'preserve this')
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'failed')
        model.release.set()
        resumed = (await (await session.run.resume(run.id, agent_id='worker')).wait(timeout=10))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(len(model.requests), 3)
        self.assertEqual([m['content'] for m in model.requests[-1]['messages']].count('preserve this'), 1)

    async def test_redirect_cannot_reset_iteration_budget(self):
        model = BlockedModel()
        _, session, request, run = await self.setup_backend(model, options={'max_iterations': 1})
        await session.run.redirect(run.id, 'again')
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)

    async def test_normal_instruction_can_prevent_first_tool(self):
        effects = []
        async def act(args):
            effects.append(args)
        component = RuntimeTools(ToolRegistry([Tool('act', 'test', {'type': 'object'}, act, contract=TEST_CONTRACT)]))
        model = loop_tests.Model([chunk(calls=[call('{}', name='act')], finish='tool_calls')],
                                 [chunk('replanned', finish='stop')])
        _, session, request, run = await self.setup_backend(model, components=[component])
        await session.run.steer(run.id, 'do not act')
        model.release.set()
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual(effects, [])
        self.assertFalse(any(s.kind == 'tool' for s in await run.steps.alist()))

    async def test_withdraw_replace_and_reject_selected_instruction(self):
        model = BlockedModel()
        _, session, request, run = await self.setup_backend(model)
        old = await session.run.steer(run.id, 'obsolete instruction')
        replacement = await session.run.replace_instruction(run.id, old.id, 'replacement', request_key='replace')
        duplicate = await session.run.replace_instruction(run.id, old.id, 'replacement', request_key='replace')
        self.assertEqual(duplicate.id, replacement.id)
        removed = await session.run.steer(run.id, 'withdraw me')
        receipt = await session.run.withdraw_instruction(run.id, removed.id)
        self.assertEqual((receipt.status, receipt.reason), ('unapplied', 'withdrawn'))
        model.release.set()
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'completed', result.data.error)
        contents = [m['content'] for m in model.requests[-1]['messages']]
        self.assertIn('replacement', contents)
        self.assertNotIn('obsolete instruction', contents)
        self.assertNotIn('withdraw me', contents)
        with self.assertRaises(ValueError):
            await session.run.withdraw_instruction(run.id, replacement.id)

    async def test_tool_cancel_no_effect_uncertain_and_completed_race(self):
        for effect, suppress in ((ToolEffect.READ_ONLY, False), (ToolEffect.EXTERNAL, False),
                                 (ToolEffect.EXTERNAL, True)):
            with self.subTest(effect=effect, suppress=suppress):
                entered, cleaned = asyncio.Event(), asyncio.Event()
                executions = []
                async def act(args):
                    executions.append('start')
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if suppress:
                            return {'actual_result': 'completed'}
                        raise
                    finally:
                        cleaned.set()
                contract = replace(TEST_CONTRACT, effect=effect)
                component = RuntimeTools(ToolRegistry([Tool('act', 'test', {'type': 'object'}, act, contract=contract)]))
                model = loop_tests.Model([chunk(calls=[call('{}', name='act')], finish='tool_calls')],
                                         [chunk('replanned', finish='stop')], gate=False)
                _, session, request, run = await self.setup_backend(model, components=[component])
                await asyncio.wait_for(entered.wait(), 10)
                await session.run.redirect(run.id, 'new plan')
                result = (await request.wait(timeout=10))[0]
                self.assertTrue(cleaned.is_set())
                self.assertEqual(executions, ['start'])
                if effect == ToolEffect.EXTERNAL and not suppress:
                    self.assertEqual(result.data.status, 'failed')
                    self.assertEqual(len(model.requests), 1)
                    with self.assertRaises(ValueError):
                        await session.run.resume(run.id, agent_id='worker')
                else:
                    self.assertEqual(result.data.status, 'completed', result.data.error)
                    content = next(m['content'] for m in model.requests[-1]['messages'] if m['role'] == 'tool')
                    self.assertIn('actual_result' if suppress else 'cancelled_no_effect', content)

    async def test_redirect_never_erases_uncertainty_of_an_earlier_attempt(self):
        from llm.services.composition import BackendServices
        from llm.services.runtime.tools import ToolRuntime, ToolExecutionError
        entered = asyncio.Event()
        attempts = []
        async def act(arguments):
            attempts.append(arguments)
            if len(attempts) == 1:
                raise ToolExecutionError('uncertain first effect', retryable=True)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise ToolExecutionError('no effect in this attempt', effect='none') from None
        component = RuntimeTools(ToolRegistry([
            Tool('act', 'test', {'type': 'object'}, act,
                 contract=replace(TEST_CONTRACT, effect=ToolEffect.EXTERNAL))]))
        model = loop_tests.Model([chunk(calls=[call('{}', name='act')], finish='tool_calls')],
                                 [chunk('must not replan', finish='stop')], gate=False)
        _, session, request, run = await self.setup_backend(model, components=[component],
            config={'policies': {'tool_retry': {'max_retries': 1}}},
            services=BackendServices(tool_runtime=ToolRuntime(retry_safe_tools=('act',))))
        await asyncio.wait_for(entered.wait(), 10)
        await session.run.redirect(run.id, 'new direction')
        result = (await request.wait(timeout=10))[0]
        self.assertEqual(result.data.status, 'failed', result.data.error)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await result.aresult()).diagnostic.details['effect'], 'uncertain')


class GraphDirectionTests(unittest.IsolatedAsyncioTestCase):
    setup_graph = graph_tests.GraphSteeringTests.setup_graph
    release_model = graph_tests.GraphSteeringTests.release_model
    entered = graph_tests.GraphSteeringTests.entered
    completed = graph_tests.GraphSteeringTests.completed
    gated = reservation_tests.ReservationTests.gated

    async def test_replace_bound_reservation_keeps_original_execution(self):
        from llm.engines.base import BaseEngine
        model = loop_tests.Model([chunk('done', finish='stop')], gate=False)
        routes = await self.gated({'writer': model})
        value = await self.session.run.reserve_instruction(self.run.id, 'superseded', targets=routes)
        bound, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        original = BaseEngine.open_instructions.__func__
        async def open_after_bind(cls, context, **kwargs):
            if context.steering.scope is not None and not context.steering.opened:
                bound.set()
                await release.wait()
            async for event in original(cls, context, **kwargs):
                yield event
        with patch.object(BaseEngine, 'open_instructions', classmethod(open_after_bind)):
            self.gate_release.set()
            await asyncio.wait_for(bound.wait(), 10)
            replacement = await self.session.run.replace_instruction(self.run.id, value.id, 'same execution')
            release.set()
            result = await self.completed()
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual(len(model.requests), 1)
        contents = [message.get('content') for message in model.requests[0]['messages']]
        self.assertIn('same execution', contents)
        self.assertNotIn('superseded', contents)
        by_id = {item.id: item for item in await result.ainstructions()}
        self.assertEqual(by_id[replacement.id].status, 'applied')

    async def test_redirect_selected_parallel_agent_only(self):
        a, b = BlockedModel(), BlockedModel()
        await self.setup_graph({'a': a, 'b': b}, graph_tests.parallel_graph())
        targets = await self.entered(a, b)
        chosen = next(t for t in targets if t.node_id == 'a')
        await self.session.run.redirect(self.run.id, 'only a', targets=[chosen.id])
        b.release.set()
        result = await self.completed()
        self.assertFalse(a.release.is_set())
        self.assertEqual(len(a.requests), 2)
        self.assertEqual(len(b.requests), 1)
        self.assertEqual((await result.ainstructions())[0].status, 'applied')


class InvocationControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_preserves_context_bindings_for_error_and_retry_callbacks(self):
        evidence = ContextVar('invocation_evidence')
        for failing in (False, True):
            with self.subTest(failing=failing):
                original = evidence.set('before')
                try:
                    async def invoke():
                        evidence.set('captured')
                        if failing:
                            raise ValueError('fixture failure')
                        return 'done'
                    control = InvocationControl()
                    if failing:
                        with self.assertRaises(ValueError):
                            await control.call(invoke)
                    else:
                        self.assertEqual(await control.call(invoke), 'done')
                    self.assertEqual(evidence.get(), 'captured')
                finally:
                    evidence.reset(original)

    async def test_stream_preserves_final_context_bindings(self):
        evidence = ContextVar('stream_evidence')
        original = evidence.set('before')
        try:
            async def source():
                yield 'text'
                evidence.set('captured')
            self.assertEqual([value async for value in InvocationControl().stream(source())], ['text'])
            self.assertEqual(evidence.get(), 'captured')
        finally:
            evidence.reset(original)

    async def test_cancelled_model_late_output_is_never_delivered(self):
        control = InvocationControl()
        entered = asyncio.Event()
        async def source():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                yield 'obsolete tool calls'
        stream = control.stream(source())
        task = asyncio.create_task(anext(stream))
        await entered.wait()
        control.request('durable-message')
        with self.assertRaises(InvocationRedirected):
            await task
        await stream.aclose()

    async def test_builtin_shell_cancellation_terminates_process(self):
        from llm.components.tools.builtin import BuiltinTools
        control = InvocationControl()
        with tempfile.TemporaryDirectory() as folder:
            tools = BuiltinTools(Path(folder), allow_commands=True, shell=['/bin/sh', '-c'])
            try:
                tool, arguments = tools.registry.prepare('shell_execute', {
                    'command': 'echo $$ > pid; sleep 60',
                    'timeout_seconds': 15, 'max_output_bytes': 1000})
                task = asyncio.create_task(control.call(lambda: tool.handler(arguments)))
                async with asyncio.timeout(10):
                    while not (Path(folder) / 'pid').exists():
                        await asyncio.sleep(.01)
                pid = int((Path(folder) / 'pid').read_text())
                control.request('stop-command')
                with self.assertRaises(InvocationRedirected):
                    await task
                stat = Path(f'/proc/{pid}/stat')
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z')
            finally:
                await tools.close()

    async def test_stream_context_and_no_read_ahead_across_ack(self):
        control = InvocationControl()
        value = ContextVar('test', default='outside')
        seen = []
        async def source():
            token = value.set('inside')
            try:
                seen.append(1)
                yield 'first'
                seen.append(value.get())
                yield 'second'
            finally:
                value.reset(token)
        stream = control.stream(source())
        self.assertEqual(await anext(stream), 'first')
        self.assertEqual(seen, [1])
        self.assertEqual(await anext(stream), 'second')
        self.assertEqual(seen, [1, 'inside'])
        await stream.aclose()
        self.assertEqual(value.get(), 'outside')

    async def test_parent_cancellation_is_not_direction_change(self):
        control = InvocationControl()
        entered = asyncio.Event()
        async def operation():
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(control.call(operation))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(control.task)
