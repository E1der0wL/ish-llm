"""재시도 단위·Application 수정안·승인·재개를 외부 모델 없이 검증한다."""

from llm.core.model_protocol import ModelMessage, ModelRequest

from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.operations import LiteLLMOperations

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from jsonschema import Draft202012Validator
from llm.policies.retry import RetryPolicy, RetryDecision, RetryFailure, change_arguments, select_retry
from llm.providers.requests import invoke, ProviderError
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.runtime.tools import ToolRuntime, ToolExecutionScope, ToolExecutor, ToolExecutionError
from llm.engines.base import EngineEventType
from tests.llm import test_long_running, test_loop
from llm.components.tools import ToolEffect, ToolIsolation
from tests.llm.support.runtime_tools import TEST_CONTRACT


SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}, "note": {"type": ["string", "null"]}},
          "required": ["path"], "additionalProperties": False}


class RetryTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_limits_and_backoff_preserve_existing_semantics(self):
        self.assertFalse(RetryPolicy().allows(1))
        fixed = RetryPolicy(3, .5)
        exponential = RetryPolicy(4, .5, 1.5, backoff="exponential")
        self.assertEqual([fixed.delay(i) for i in (1, 2, 3)], [.5, .5, .5])
        self.assertEqual([exponential.delay(i) for i in (1, 2, 3)], [.5, 1, 1.5])
        self.assertTrue(fixed.allows(2))
        self.assertFalse(fixed.allows(3))
        with patch('llm.policies.retry.time.monotonic', return_value=10):
            self.assertEqual(RetryPolicy.end(30, 20), 20)
            self.assertIsNone(RetryPolicy.end())
            with self.assertRaises(TimeoutError):
                RetryPolicy(deadline=9).remaining()

    async def test_deadline_and_cancellation_stop_before_next_attempt(self):
        with patch('llm.policies.retry.time.monotonic', return_value=10), \
                patch('llm.policies.retry.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(TimeoutError):
                await RetryPolicy(2, 5, deadline=12).wait(1)
            sleep.assert_not_awaited()
        entered = asyncio.Event()
        async def choose(failure):
            entered.set()
            await asyncio.Event().wait()
        pending = asyncio.create_task(select_retry(RetryFailure('x', {}, 'x', 'x', 1, True), choose))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending

    def test_patch_is_copy_top_level_null_and_full_schema_validated(self):
        original = {'path': 'a', 'note': 'keep'}
        def apply(changes):
            return change_arguments(original, RetryDecision(changes), allowed=('path', 'note'),
                                    validate=Draft202012Validator(SCHEMA).validate)
        self.assertEqual(apply(None), original)
        self.assertEqual(apply({'note': None}), {'path': 'a', 'note': None})
        self.assertEqual(original, {'path': 'a', 'note': 'keep'})
        with self.assertRaises(ValueError):
            apply({'unknown': 1})
        with self.assertRaises(Exception):
            apply({'path': 1})

    async def test_provider_application_can_stop_or_change_declared_timeout(self):
        for decision, expected in ((None, 1), (RetryDecision(), 2), (RetryDecision({'timeout': 2}), 2)):
            calls = []
            async def call(**params):
                calls.append(deepcopy(params))
                if len(calls) == 1:
                    raise TimeoutError()
                return 'ok'
            def choose(failure):
                self.assertEqual(failure.attempt, 1)
                failure.arguments['timeout'] = 1000  # detached view
                return decision
            original = {'timeout': 1}
            if decision is None:
                with self.assertRaises(ProviderError):
                    await invoke('aembedding', original, call, {'max_attempts': 2}, decide=choose, changeable=('timeout',))
            else:
                self.assertEqual(await invoke('aembedding', original, call, {'max_attempts': 2},
                    decide=choose, changeable=('timeout',)), 'ok')
                self.assertEqual(calls[-1]['timeout'], 2 if decision.changes else 1)
            self.assertEqual(len(calls), expected)
            self.assertEqual(original, {'timeout': 1})

    async def test_provider_does_not_retry_without_limit_or_with_sdk_retry(self):
        for options, args in (({}, {}), ({'max_attempts': 3}, {'max_retries': 2})):
            choose = AsyncMock(return_value=RetryDecision({'timeout': 2}))
            call = AsyncMock(side_effect=TimeoutError())
            with self.assertRaises(ProviderError):
                await invoke('aembedding', args, call, options, decide=choose, changeable=('timeout',))
            self.assertEqual(call.await_count, 1)
            choose.assert_not_awaited()

    async def test_same_retry_preserves_native_runtime_timeout_object(self):
        native = object()
        calls = []
        async def call(**params):
            calls.append(params)
            if len(calls) == 1:
                raise TimeoutError()
            return 'ok'
        self.assertEqual(await invoke('aembedding', {'timeout': native}, call, {'max_attempts': 2}), 'ok')
        self.assertTrue(all(value['timeout'] is native for value in calls))

    async def test_embedding_identity_and_cache_invariant_cannot_be_repaired(self):
        from llm.components.rag import EmbeddingModel
        for changes in ({'model': 'different'}, {'input': ['different']}, {'cache': {}}, {'dimensions': 1}):
            call = AsyncMock(side_effect=TimeoutError())
            model = EmbeddingModel(model='test', adapter=LiteLLMOperations(call=call)).with_provider({'max_attempts': 2}).with_retry(
                lambda failure: RetryDecision(changes))
            with self.assertRaisesRegex(ValueError, 'cannot change'):
                await model.embed(['original'])
            self.assertEqual(call.await_count, 1)

    async def test_stream_transport_correction_and_no_retry_after_delta(self):
        from llm.engines.base import BaseEngine
        for partial in (False, True):
            calls, observed = [], []
            def stream(**params):
                calls.append(params)
                if len(calls) == 1:
                    if partial:
                        yield test_loop.chunk('partial')
                    raise TimeoutError()
                yield test_loop.chunk('ok', finish='stop')
            def choose(failure):
                observed.append(failure)
                return RetryDecision({'timeout': 2})
            async def execute():
                return [event async for event in BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=stream)).stream_completion(
                    ModelRequest([], {'model': 'test'}), provider={'max_attempts': 2}, retry_decider=choose)]
            if partial:
                with self.assertRaises(Exception):
                    await execute()
                self.assertEqual((len(calls), len(observed)), (1, 0))
            else:
                await execute()
                self.assertEqual((len(calls), len(observed)), (2, 1))
                self.assertNotIn('timeout', calls[0])
                self.assertEqual(calls[1]['timeout'], 2)

    async def test_retry_observation_precedes_backoff_and_survives_cancellation(self):
        from llm.services.infrastructure.observability import Observability
        from llm.engines.base import BaseEngine
        for streaming in (False, True):
            ready, calls = asyncio.Event(), []
            def sink(event):
                if event.get('kind') == 'providers' and event.get('status') == 'retries':
                    ready.set()
            def stream(**kwargs):
                calls.append(kwargs)
                raise TimeoutError()
                yield
            async def call(**kwargs):
                calls.append(kwargs)
                raise TimeoutError()
            async def run():
                policy = {'max_attempts': 2, 'delay_seconds': 60}
                if streaming:
                    async for _ in BaseEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=stream)).stream_completion(ModelRequest([], {'model': 'test'}), provider=policy):
                        pass
                else:
                    await invoke('aembedding', {'model': 'test'}, call, policy)
            observer = Observability(sink=sink)
            with observer.scope():
                task = asyncio.create_task(run())
                try:
                    await asyncio.wait_for(ready.wait(), 2)
                    self.assertFalse(task.done())
                    self.assertEqual(len(calls), 1)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(observer.snapshot()['providers']['retries'], 1)


class ToolRetryTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_long_running.LongRunningTests.setup_app

    def test_only_injected_selector_adds_binding_authority(self):
        plain = ToolExecutionScope(ToolRuntime()).binding()
        selected = ToolExecutionScope(ToolRuntime(decide_retry=lambda failure: None)).binding()
        self.assertNotIn('retry_selector', plain)
        self.assertEqual(selected, {**plain, 'retry_selector': True})

    async def test_reserved_checkpoint_namespace_cannot_collide_with_custom_engine(self):
        handler = AsyncMock()
        tool = Tool('read', 'read', SCHEMA, handler, contract=TEST_CONTRACT)
        with self.assertRaisesRegex(ValueError, 'reserved retry namespace'):
            async for _ in ToolExecutor().execute(tool, {'path': 'a'}, result={}, request_key='@tool-retry:x'):
                pass
        handler.assert_not_awaited()

    async def execute(self, handler, decide, *, retries=1, contract=None, policy=None, runtime=None):
        scope = ToolExecutionScope(runtime or ToolRuntime(decide_retry=decide),
            retry={} if retries is None else {'max_retries': retries}, policy=policy)
        context = SimpleNamespace(project=SimpleNamespace(id='p'), session=SimpleNamespace(id='s'),
            run=SimpleNamespace(id='r', input_message_id='m', metadata={}), tool_scope=scope)
        result, events = {}, []
        tool = Tool('read', 'read', SCHEMA, handler, contract=contract or ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=('path',)))
        async for event in ToolExecutor().execute(tool, {'path': 'bad'}, result=result, context=context):
            events.append(event)
        return result, events, scope

    async def test_corrected_request_revalidates_and_records_without_failed_step(self):
        calls, classifications = [], []
        async def handler(args):
            calls.append(args)
            if args['path'] == 'bad':
                raise ToolExecutionError('missing path', effect='none', recoverable=True)
            return None
        from llm.components.tools import ToolClassification
        def classify(call):
            classifications.append(call.arguments)
            return ToolClassification()
        result, events, scope = await self.execute(handler, None, runtime=ToolRuntime(
            decide_retry=lambda failure: RetryDecision({'path': 'good'}), classify=classify))
        self.assertEqual(calls, [{'path': 'bad'}, {'path': 'good'}])
        self.assertEqual(classifications, calls)
        self.assertEqual((scope.calls, scope.completed), (2, 1))
        self.assertIsNone(result['value'])
        self.assertEqual(result['retry']['repairs'], 1)
        self.assertEqual(len(result['retry']['failures']), 1)
        self.assertNotIn(EngineEventType.STEP_FAILED, [e.type for e in events])

    async def test_missing_policy_stop_unknown_and_uncertain_do_not_repeat(self):
        for error, retries, choose in (
            (ToolExecutionError('missing', effect='none', recoverable=True), None, RetryDecision({'path': 'good'})),
            (ToolExecutionError('missing', effect='none', recoverable=True), 2, None),
            (RuntimeError('unknown'), 2, RetryDecision({'path': 'good'})),
            (ToolExecutionError('uncertain', effect='uncertain'), 2, RetryDecision({'path': 'good'}))):
            handler = AsyncMock(side_effect=error)
            with self.assertRaises(Exception):
                await self.execute(handler, lambda failure: choose, retries=retries)
            self.assertEqual(handler.await_count, 1)

    async def test_patch_cannot_widen_fields_schema_or_project_constraints(self):
        for changes, policy in (({'note': 'forbidden'}, None), ({'path': 7}, None),
                ({'path': 'good'}, {'argument_constraints': {'read': {'path': {'mode': 'fixed', 'value': 'bad'}}}})):
            handler = AsyncMock(side_effect=ToolExecutionError('missing', effect='none', recoverable=True))
            with self.assertRaises(Exception):
                await self.execute(handler, lambda failure: RetryDecision(changes), policy=policy)
            self.assertEqual(handler.await_count, 1)

    async def test_application_cannot_hide_changed_arguments_from_authorization(self):
        seen = []
        async def authorize(call):
            seen.append(call.arguments['path'])
            return call.arguments['path'] != 'good'
        handler = AsyncMock(side_effect=ToolExecutionError('missing', effect='none', recoverable=True))
        with self.assertRaisesRegex(Exception, 'denied'):
            await self.execute(handler, None, runtime=ToolRuntime(authorize=authorize,
                decide_retry=lambda failure: RetryDecision({'path': 'good'})))
        self.assertEqual(seen, ['bad', 'good'])
        self.assertEqual(handler.await_count, 1)

    async def test_loop_approval_resume_preserves_patch_budget_and_exactly_one_effect(self):
        calls, choices = [], []
        async def handler(args):
            calls.append(args['path'])
            if args['path'] == 'bad':
                raise ToolExecutionError('missing', effect='none', recoverable=True)
            return 'done'
        def choose(failure):
            choices.append(failure.attempt)
            return RetryDecision({'path': 'good'})
        app, session, model = await self.setup_app(handler, [
            [test_loop.chunk(calls=[test_loop.call('{"path":"bad"}', name='act')], finish='tool_calls')],
            [test_loop.chunk('finished', finish='stop')]], ToolRuntime(decide_retry=choose),
            policies={'tool_retry': {'max_retries': 1}}, tool_schema=SCHEMA,
            tool_contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=('path',)))
        initial = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(initial.data.status, 'paused', initial.data.error)
        self.assertEqual(calls, [])
        request, = await initial.ainteractions(pending_only=True)
        await initial.arespond(request.respond('approve'))
        repaired = await (await session.run.resume(initial.id, engine='loop')).wait()
        self.assertEqual(repaired.data.status, 'paused', repaired.data.error)
        self.assertEqual(calls, ['bad'])
        request, = await repaired.ainteractions(pending_only=True)
        self.assertEqual(request.action['arguments'], {'path': 'good'})
        self.assertEqual(choices, [1])
        await repaired.arespond(request.respond('approve'))
        resumed = await (await session.run.resume(repaired.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(calls, ['bad', 'good'])
        self.assertEqual(choices, [1])
        self.assertEqual(len(model.requests), 2)
        state = await resumed.acheckpoint('loop')
        retry_record = next(v['retry'] for v in state['records'].values() if 'retry' in v)
        self.assertEqual((retry_record['attempt'], retry_record['repairs']), (1, 1))

    async def test_failed_repair_budget_cannot_reset_on_explicit_resume(self):
        calls = []
        async def handler(args):
            calls.append(args['path'])
            raise ToolExecutionError('missing', effect='none', recoverable=True)
        app, session, _ = await self.setup_app(handler, [
            [test_loop.chunk(calls=[test_loop.call('{"path":"bad"}', name='act')], finish='tool_calls')]],
            ToolRuntime(decide_retry=lambda failure: RetryDecision({'path': 'next'})),
            policies={'tool_retry': {'max_retries': 1}}, tool_schema=SCHEMA,
            tool_contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=('path',)))
        failed = await (await session.run.submit('go', engine='loop')).wait()
        self.assertEqual(failed.data.status, 'failed')
        self.assertEqual(calls, ['bad', 'next'])
        resumed = await (await session.run.resume(failed.id, engine='loop')).wait()
        self.assertEqual(resumed.data.status, 'failed')
        self.assertIn('retry limit', resumed.data.error)
        self.assertEqual(calls, ['bad', 'next'])

    async def test_builtin_missing_file_can_be_corrected_without_weakening_path_protection(self):
        from llm.components.tools.builtin import BuiltinTools
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'real.txt').write_text('contents')
            async with BuiltinTools(directory) as builtin:
                tool = builtin.registry.get('file_read')
                result, _, _ = await self.execute(tool.handler,
                    lambda failure: RetryDecision({'path': 'real.txt'}), contract=tool.contract)
                self.assertEqual(result['value']['text'], 'contents')
                with self.assertRaises(Exception):
                    await self.execute(tool.handler, lambda failure: RetryDecision({'path': '../escape'}), contract=tool.contract)

    async def test_cancellation_during_selector_never_executes_next_attempt(self):
        entered = asyncio.Event()
        async def choose(failure):
            entered.set()
            await asyncio.Event().wait()
        handler = AsyncMock(side_effect=ToolExecutionError('missing', effect='none', recoverable=True))
        pending = asyncio.create_task(self.execute(handler, choose))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(handler.await_count, 1)

    async def test_graph_corrected_approval_survives_backend_restart(self):
        from llm.llm import LargeLanguageModel, BackendServices, ProjectConfig
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.tool import ToolNode
        from llm.components.workflows import WorkflowComponent, WorkflowGraph
        from tests.llm.support.runtime_tools import RuntimeTools
        calls, choices = [], []
        async def handler(args):
            calls.append(args['path'])
            if args['path'] == 'bad':
                raise ToolExecutionError('missing', effect='none', recoverable=True)
            return 'done'
        def choose(failure):
            choices.append(failure.attempt)
            return RetryDecision({'path': 'good'})
        def backend(root):
            return LargeLanguageModel(root,
                components=[RuntimeTools(ToolRegistry((Tool('act', 'action', SCHEMA, handler,
                    contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=True, operation_key_required=False, retry_fields=('path',))),))), WorkflowComponent()],
                engines={'graph': GraphEngine(handlers={'tool': ToolNode()})},
                services=BackendServices(tool_runtime=ToolRuntime(decide_retry=choose)))
        with tempfile.TemporaryDirectory() as directory:
            app = backend(Path(directory))
            self.addAsyncCleanup(app.shutdown)
            project = await app.projects.acreate(components=['tools', 'workflows'],
                config=ProjectConfig(policies={'tool_retry': {'max_retries': 1}}))
            project.components.tools.enable('act')
            graph = (WorkflowGraph(entry='action').node('action', 'tool', tool='act', arguments={'path': 'bad'})
                .node('end', 'end').connect('action', 'end').to_dict())
            await project.components.workflows.acreate(graph, identifier='flow')
            session = await project.sessions.acreate()
            paused = await (await session.run.submit('go', engine='graph', engine_options={'workflow': 'flow'})).wait()
            self.assertEqual(paused.data.status, 'paused', paused.data.error)
            request, = await paused.ainteractions(pending_only=True)
            await paused.arespond(request.respond('approve'))
            repaired = await (await session.run.resume(paused.id, engine='graph')).wait()
            self.assertEqual(repaired.data.status, 'paused', repaired.data.error)
            request, = await repaired.ainteractions(pending_only=True)
            self.assertEqual(request.action['arguments'], {'path': 'good'})
            await repaired.arespond(request.respond('approve'))
            project_id, session_id = project.id, session.id
            await app.shutdown()
            reopened = backend(Path(directory))
            self.addAsyncCleanup(reopened.shutdown)
            session = (await reopened.projects.aload(project_id)).sessions.load(session_id)
            complete = await (await session.run.resume(repaired.id, engine='graph')).wait()
            self.assertEqual(complete.data.status, 'completed', complete.data.error)
            self.assertEqual(calls, ['bad', 'good'])
            self.assertEqual(choices, [1])
            await reopened.shutdown()

    async def test_corrected_operation_requires_a_nonconflicting_business_key(self):
        for same_key in (False, True):
            calls = []
            async def handler(args):
                calls.append(args['path'])
                if args['path'] == 'bad':
                    raise ToolExecutionError('missing', effect='none', recoverable=True)
                return 'done'
            app, session, _ = await self.setup_app(handler, [
                [test_loop.chunk(calls=[test_loop.call('{"path":"bad"}', name='act')], finish='tool_calls')],
                [test_loop.chunk('done', finish='stop')]],
                ToolRuntime(operation_key=lambda call: 'work' if same_key else call.arguments['path'],
                    decide_retry=lambda failure: RetryDecision({'path': 'good'})),
                policies={'tool_retry': {'max_retries': 1}}, tool_schema=SCHEMA,
                tool_contract=ToolContract(revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=True, retry_fields=('path',)))
            run = await (await session.run.submit('go', engine='loop')).wait()
            receipt = await session.run.aoperation('work' if same_key else 'bad')
            self.assertEqual(receipt['status'], 'not_applied')
            if same_key:
                self.assertEqual(run.data.status, 'failed')
                self.assertEqual(calls, ['bad'])
            else:
                self.assertEqual(run.data.status, 'completed', run.data.error)
                self.assertEqual(calls, ['bad', 'good'])
                self.assertEqual((await session.run.aoperation('good'))['status'], 'completed')
