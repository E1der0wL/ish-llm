"""Explicit Tool feedback keeps failed Steps and never grants execution authority."""

import json
import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

from llm.services.runtime.tools import ToolExecutionError, ToolRuntime
from tests.llm.configuration_fixtures import configure_engine
from tests.llm.support.runtime_tools import TEST_CONTRACT
from tests.llm import test_long_running
from tests.llm.test_loop import chunk, call


class ToolFeedbackTests(unittest.IsolatedAsyncioTestCase):
    setup_app = test_long_running.LongRunningTests.setup_app

    async def execute(self, error, *, feedback=None, runtime=None, calls=None, contract=TEST_CONTRACT):
        effects = []
        async def act(arguments):
            effects.append(arguments)
            if not arguments.get('ok'):
                raise error
            return 'ok'
        app, session, model = await self.setup_app(act, [
            [chunk(calls=calls or [call('{}', name='act')], finish='tool_calls')],
            [chunk('alternative', finish='stop')]], runtime=runtime, tool_contract=contract)
        if feedback is not None:
            await configure_engine(session.project, 'loop', tool_feedback=feedback)
        handle = (await (await session.run.submit('go', recipients=['loop'])).wait())[0]
        return app, session, model, handle, effects

    async def test_expected_failure_feedback_preserves_failed_step(self):
        app, session, model, handle, effects = await self.execute(
            ToolExecutionError('missing file', effect='none', recoverable=True),
            feedback={'failure': True, 'remaining_calls': 'defer'})
        self.assertEqual(handle.data.status, 'completed', handle.data.error)
        steps = await handle.steps.alist()
        failed = [s for s in steps if s.status == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertIn('handled_failure', failed[0].metadata)
        payload = json.loads(model.requests[-1]['messages'][-1]['content'])
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual(payload['diagnostic']['details']['effect'], 'none')
        self.assertEqual(len(effects), 1)

    async def test_no_policy_and_uncertain_or_unknown_errors_still_fail(self):
        for error, feedback in (
            (ToolExecutionError('no policy', effect='none'), None),
            (ToolExecutionError('uncertain', effect='uncertain'), {'failure': True, 'remaining_calls': 'continue'}),
            (RuntimeError('unknown'), {'failure': True, 'remaining_calls': 'continue'}),
        ):
            with self.subTest(error=str(error)):
                _, _, model, handle, effects = await self.execute(error, feedback=feedback)
                self.assertEqual(handle.data.status, 'failed')
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(len(effects), 1)

    async def test_application_selects_remaining_calls_behavior(self):
        for behavior, count, status in [('defer', 1, 'not_executed'), ('continue', 2, None)]:
            with self.subTest(behavior=behavior):
                _, _, model, handle, effects = await self.execute(
                    ToolExecutionError('not applied', effect='none'),
                    feedback={'failure': True, 'remaining_calls': behavior},
                    calls=[call('{}', name='act', call_id='a'),
                           call('{"ok": true}', name='act', call_id='b', index=1)])
                self.assertEqual(handle.data.status, 'completed', handle.data.error)
                self.assertEqual(len(effects), count)
                replies = [m for m in model.requests[-1]['messages'] if m['role'] == 'tool']
                self.assertEqual([m['tool_call_id'] for m in replies], ['a', 'b'])
                self.assertEqual(json.loads(replies[1]['content']).get('status') if status else
                                 replies[1]['content'], status or 'ok')

    async def test_denial_feedback_requires_explicit_resume(self):
        _, session, model, paused, effects = await self.execute(
            RuntimeError('must not run'),
            feedback={'declined': True, 'remaining_calls': 'defer'},
            contract=replace(TEST_CONTRACT, approval_required=True))
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        request = (await paused.ainteractions())[0]
        await paused.arespond(request.respond('deny'))
        self.assertEqual(effects, [])
        plan = await session.run.resume_plan(paused.id, agent_id='loop')
        self.assertTrue(plan.can_resume, plan)
        resumed = (await (await session.run.resume(paused.id, agent_id='loop')).wait())[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [])
        self.assertEqual(json.loads(model.requests[-1]['messages'][-1]['content'])['status'], 'declined')

    async def test_host_technical_denial_is_not_model_feedback(self):
        _, _, model, handle, effects = await self.execute(
            RuntimeError('must not run'),
            feedback={'failure': True, 'declined': True, 'remaining_calls': 'defer'},
            runtime=ToolRuntime(authorize=lambda call: False))
        self.assertEqual(handle.data.status, 'failed')
        self.assertEqual(effects, [])
        self.assertEqual(len(model.requests), 1)

    async def test_cancellation_is_not_feedback(self):
        entered = asyncio.Event()
        async def act(arguments):
            entered.set()
            await asyncio.Future()
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('must not run', finish='stop')]])
        await configure_engine(session.project, 'loop', tool_feedback={'failure': True, 'remaining_calls': 'defer'})
        request = await session.run.submit('go', recipients=['loop'])
        await asyncio.wait_for(entered.wait(), 10)
        await session.run.interrupt()
        run, = await request.wait()
        self.assertEqual(run.data.status, 'interrupted')
        self.assertEqual(len(model.requests), 1)
        self.assertFalse(any('handled_failure' in s.metadata for s in await run.steps.alist()))

    async def test_prior_uncertain_retry_cannot_be_reported_as_no_effect(self):
        from llm.policies.retry import RetryDecision
        attempts = []
        async def act(arguments):
            attempts.append(arguments)
            raise ToolExecutionError('uncertain then no effect', retryable=True,
                                     effect='uncertain' if len(attempts) == 1 else 'none')
        runtime = ToolRuntime(retry_safe_tools=('act',),
                              decide_retry=lambda failure: RetryDecision() if len(attempts) == 1 else None)
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('must not run', finish='stop')]],
            runtime=runtime, policies={'tool_retry': {'max_retries': 2}})
        await configure_engine(session.project, 'loop', tool_feedback={'failure': True, 'remaining_calls': 'defer'})
        run, = await (await session.run.submit('go', recipients=['loop'])).wait()
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual((await run.aresult()).diagnostic.details['effect'], 'uncertain')

    async def test_feedback_requires_explicit_batch_choice(self):
        from llm.engines.loop.configuration import ToolFeedbackPolicy
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):
            ToolFeedbackPolicy({'failure': True})
        ToolFeedbackPolicy({})

    async def test_handled_failure_is_reused_after_provider_failure(self):
        attempts = []
        async def act(arguments):
            attempts.append(arguments)
            raise ToolExecutionError('missing', effect='none')
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [ConnectionError('after feedback')], [chunk('recovered', finish='stop')]])
        await configure_engine(session.project, 'loop', tool_feedback={'failure': True, 'remaining_calls': 'defer'})
        failed = (await (await session.run.submit('go', recipients=['loop'])).wait())[0]
        self.assertEqual(failed.data.status, 'failed')
        resumed = (await (await session.run.resume(failed.id, agent_id='loop')).wait())[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(attempts, [{}])
        self.assertEqual(json.loads(model.requests[-1]['messages'][-1]['content'])['status'], 'failed')

    async def test_handling_persistence_failure_prevents_next_model_call(self):
        async def act(arguments):
            raise ToolExecutionError('missing', effect='none')
        app, session, model = await self.setup_app(act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('must not run', finish='stop')]])
        await configure_engine(session.project, 'loop', tool_feedback={'failure': True, 'remaining_calls': 'defer'})
        original = app.step_manager.save
        def save(step):
            if 'handled_failure' in step.metadata:
                raise OSError('disk full')
            return original(step)
        with patch.object(app.step_manager, 'save', side_effect=save):
            run = (await (await session.run.submit('go', recipients=['loop'])).wait())[0]
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(len(model.requests), 1)
        failed = [s for s in await run.steps.alist() if s.kind == 'tool'][0]
        self.assertEqual(failed.status, 'failed')
        self.assertNotIn('handled_failure', failed.metadata)


class NestedFeedbackTests(unittest.IsolatedAsyncioTestCase):
    from tests.llm import test_nested_graph as fixture
    asyncSetUp = fixture.NestedGraphTests.asyncSetUp
    setup = fixture.NestedGraphTests.setup
    request = fixture.NestedGraphTests.request

    async def test_nested_graph_agent_denial_is_feedback_only_when_selected(self):
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from llm.llm import BackendServices
        from llm.services.runtime.tools import ToolApprovalRequired
        from tests.llm.test_loop import ScriptedCompletion
        async def authorize(call):
            raise ToolApprovalRequired('review')
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')],
                                   [chunk('ask the user instead', finish='stop')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': self.fixture.action_graph('agent', agent='worker')}, handlers={'agent': handler},
                         services=BackendServices(tool_runtime=ToolRuntime(authorize=authorize)))
        await self.agents.acreate({'engine': 'loop', 'completion': {'model': 'test'}, 'tools': ['effect'],
            'engine_options': {'policy': {'tool_feedback': {'declined': True, 'remaining_calls': 'defer'}}}}, identifier='worker')
        paused = await self.request()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        request = (await paused.ainteractions())[0]
        self.assertTrue(request.checkpoint_binding.resume_on_denial)
        await paused.arespond(request.respond('deny'))
        plan = await self.session.run.resume_plan(paused.id, agent_id='main-agent')
        self.assertTrue(plan.can_resume, plan)
        done = (await (await self.session.run.resume(paused.id, agent_id='main-agent')).wait())[0]
        self.assertEqual(done.data.status, 'completed', done.data.error)
        self.assertEqual(self.effects, [])
        self.assertEqual(len(model.requests), 2)

    async def test_nested_graph_agent_handles_failed_step_without_changing_status(self):
        from llm.engines.graph.agent import AgentNode
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from llm.components.tools import Tool, ToolRegistry
        from tests.llm.test_loop import ScriptedCompletion
        async def fail(arguments):
            raise ToolExecutionError('missing', effect='none')
        self.tools = ToolRegistry([Tool('effect', 'test', {'type': 'object'}, fail, contract=TEST_CONTRACT)])
        model = ScriptedCompletion([chunk(calls=[call('{}', name='effect')], finish='tool_calls')],
                                   [chunk('alternative', finish='stop')])
        handler = AgentNode(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        await self.setup({'main': self.fixture.action_graph('agent', agent='worker')}, handlers={'agent': handler})
        await self.agents.acreate({'engine': 'loop', 'completion': {'model': 'test'}, 'tools': ['effect'],
            'engine_options': {'policy': {'tool_feedback': {'failure': True, 'remaining_calls': 'defer'}}}}, identifier='worker')
        done = await self.request()
        self.assertEqual(done.data.status, 'completed', done.data.error)
        failed = [s for s in await done.steps.alist() if s.status == 'failed']
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].metadata['handled_failure']['checkpoint'], 'graph')

