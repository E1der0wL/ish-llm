"""Per-invocation visibility never widens authority or replays completed effects."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools import Tool, ToolRegistry, ToolExposure
from llm.core.model_protocol import ModelMessage
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, BackendServices
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.services.runtime.tools import ToolRuntime
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class Selector:
    revision = 'test-selection-v1'

    def __init__(self, choices):
        self.choices = choices
        self.requests = []

    async def select_tools(self, request):
        self.requests.append(request)
        return self.choices[request.iteration]


class ToolExposureTests(unittest.TestCase):
    def test_definitions_subset_does_not_register_or_copy_unselected_tools(self):
        async def act(args): pass
        registry = ToolRegistry(tuple(Tool(name, name, {'type': 'object'}, act, contract=TEST_CONTRACT)
                                      for name in ('first', 'second')))
        with patch.object(registry, 'register', side_effect=AssertionError('no registration')):
            selected = registry.definitions(names=['second'])
            self.assertEqual([v.name for v in selected], ['second'])
            self.assertEqual(registry.definitions(names=[]), ())
            selected[0].parameters['properties'] = {'changed': {'type': 'string'}}
            self.assertNotIn('properties', registry.get('second').parameters)
        self.assertEqual(registry.names(), ('first', 'second'))

    def test_invalid_choices_fail_without_default_or_authority_expansion(self):
        request = ToolExposure(1, (), ('first', 'second'), False)
        for value in (None, 'first', {'first'}, ['first', 'first'], ['unknown'], [1]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                request.validate(value)
        self.assertEqual(request.validate([]), ())
        self.assertEqual(request.validate(['second', 'first']), ('second', 'first'))
        with self.assertRaisesRegex(ValueError, 'required'):
            replace(request, required=True).validate([])

    def test_selector_requires_explicit_revision_and_public_method(self):
        for selector in (object(), lambda value: [], type('MissingRevision', (), {'select_tools': lambda *_: []})(),
                         type('MissingMethod', (), {'revision': '1'})()):
            with self.subTest(selector=type(selector)), self.assertRaises(TypeError):
                LoopEngine(tool_selector=selector)
        self.assertIsNone(LoopEngine().tool_selector)

    def test_exposure_input_is_detached(self):
        message = ModelMessage('user', 'keep me')
        request = ToolExposure(1, (message,), ('first',), False)
        self.assertIsNot(request.messages[0], message)
        self.assertEqual(request.messages[0].text, 'keep me')


class LoopToolExposureTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, scripts, *, selector=None, approval=(), policies=None,
                    require_tool=False, runtime=None, agent_tools=('first', 'second')):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.effects = []
        async def first(args):
            self.effects.append(('first', args))
            return 'first completed'
        async def second(args):
            self.effects.append(('second', args))
            return 'second completed'
        schema = {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'additionalProperties': False}
        registry = ToolRegistry(tuple(Tool(name, name, schema, handler,
            contract=replace(TEST_CONTRACT, approval_required=name in approval))
            for name, handler in (('first', first), ('second', second))))
        model = ScriptedCompletion(*scripts)
        engine = LoopEngine(tool_selector=selector, completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))
        app = LargeLanguageModel(directory.name, engines={'loop': engine}, components=[RuntimeTools(registry)],
                                 services=BackendServices(tool_runtime=runtime) if runtime is not None else None)
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate('exposure', components=['tools'], config={'policies': policies or {}})
        await project.components.tools.aenable(*registry.names())
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/model'},
            'tools': list(agent_tools), 'policy': {'require_tool': require_tool}}, identifier='worker')
        session = await project.sessions.acreate('one', agent_ids=['worker'])
        return app, session, model, engine

    async def submit(self, session, text='request'):
        return (await (await session.run.submit(text, recipients=['worker'])).wait(timeout=15))[0]

    @staticmethod
    def names(request):
        return [row['function']['name'] for row in request.get('tools', [])]

    async def test_each_call_selects_independently_and_preserves_tool_history(self):
        selector = Selector({1: ['first'], 2: ['second'], 3: []})
        _, session, model, _ = await self.setup([
            [chunk(calls=[call('{}', name='first')], finish='tool_calls')],
            [chunk(calls=[call('{}', name='second', call_id='call_2')], finish='tool_calls')],
            [chunk('done', finish='stop')]], selector=selector)
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual([self.names(r) for r in model.requests], [['first'], ['second'], []])
        self.assertEqual([name for name, _ in self.effects], ['first', 'second'])
        self.assertIn('first completed', str(model.requests[-1]['messages']))
        self.assertIn('second completed', str(model.requests[-1]['messages']))
        self.assertTrue(all(r.available == ('first', 'second') for r in selector.requests))
        self.assertEqual([r.iteration for r in selector.requests], [1, 2, 3])
        checkpoint = await run.acheckpoint('loop')
        self.assertEqual(checkpoint['records']['iteration:1']['tools'], ['first'])
        self.assertEqual(checkpoint['records']['iteration:1']['status'], 'completed')

    async def test_no_selector_preserves_existing_tools_and_explicit_empty_hides_all(self):
        for selector, names in ((None, ['first', 'second']), (Selector({1: []}), [])):
            with self.subTest(selector=selector):
                _, session, model, _ = await self.setup([[chunk('done', finish='stop')]], selector=selector)
                run = await self.submit(session)
                self.assertEqual(run.data.status, 'completed', run.data.error)
                self.assertEqual(self.names(model.requests[0]), names)
                self.assertEqual(self.effects, [])

    async def test_unexposed_call_rejects_entire_batch_before_any_effect(self):
        _, session, _, _ = await self.setup([[chunk(calls=[call('{}', name='first'),
            call('{}', name='second', call_id='call_2', index=1)], finish='tool_calls')]],
            selector=Selector({1: ['first']}))
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'failed')
        self.assertIn('not exposed', run.data.error)
        self.assertEqual(self.effects, [])
        self.assertFalse(any(s.kind == 'tool' for s in await run.steps.alist()))

    async def test_invalid_selection_and_require_tool_conflict_do_not_call_provider(self):
        for names, required in ((['missing'], False), (['first', 'first'], False),
                                (None, False), ([], True)):
            with self.subTest(names=names, required=required):
                _, session, model, _ = await self.setup([], selector=Selector({1: names}), require_tool=required)
                run = await self.submit(session)
                self.assertEqual(run.data.status, 'failed')
                self.assertEqual(model.requests, [])
                self.assertEqual(self.effects, [])

    async def test_selector_cannot_expand_agent_scope(self):
        selector = Selector({1: ['second']})
        _, session, model, _ = await self.setup([], selector=selector, agent_tools=('first',))
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(selector.requests[0].available, ('first',))
        self.assertEqual(model.requests, [])

    async def test_constraints_remain_in_exposed_schema_and_runtime_validation(self):
        policies = {'tools': {'argument_constraints': {'first': {'value': {'mode': 'fixed', 'value': 4}}}}}
        _, session, model, _ = await self.setup([[chunk(calls=[call('{"value":5}', name='first')],
            finish='tool_calls')]], selector=Selector({1: ['first']}), policies=policies)
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'failed')
        self.assertEqual(self.effects, [])
        self.assertIn('4', str(model.requests[0]['tools']))

    async def test_approval_resume_restores_selection_and_does_not_replay_completed_tool(self):
        selector = Selector({1: ['first'], 2: ['second'], 3: []})
        _, session, model, _ = await self.setup([
            [chunk(calls=[call('{}', name='first')], finish='tool_calls')],
            [chunk(calls=[call('{}', name='second', call_id='call_2')], finish='tool_calls')],
            [chunk('done', finish='stop')]], selector=selector, approval=('second',))
        paused = await self.submit(session)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(self.effects, [('first', {})])
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('approve'))
        # Completed/resumable invocations must not call the selector again.
        selector.choices = {3: []}
        resumed = (await (await session.run.resume(paused.id, agent_id='worker')).wait(timeout=15))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [('first', {}), ('second', {})])
        self.assertEqual([r.iteration for r in selector.requests], [1, 2, 3])
        self.assertEqual(len(model.requests), 3)

    async def test_changed_selector_revision_blocks_approved_resume(self):
        selector = Selector({1: ['first']})
        _, session, model, _ = await self.setup([[chunk(calls=[call('{}', name='first')], finish='tool_calls')]],
            selector=selector, approval=('first',))
        paused = await self.submit(session)
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('approve'))
        selector.revision = 'changed-selection-v2'
        with self.assertRaisesRegex(Exception, 'changed'):
            await session.run.resume(paused.id, agent_id='worker')
        self.assertEqual(self.effects, [])
        self.assertEqual(len(model.requests), 1)

    async def test_saved_exposure_is_validated_not_inferred_on_resume(self):
        _, session, _, engine = await self.setup([[chunk(calls=[call('{}', name='first')], finish='tool_calls')]],
            selector=Selector({1: ['first']}), approval=('first',))
        paused = await self.submit(session)
        checkpoint = await paused.acheckpoint('loop')
        for changes in ({'tools': []}, {'tools': ['first', 'first']}, {'tool_mode': 'invalid'}):
            damaged = deepcopy(checkpoint)
            damaged['records']['iteration:1'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine.validate_resume(damaged, decisions={'tool:1:call_1': True})
        damaged = deepcopy(checkpoint)
        del damaged['records']['iteration:1']['tools']
        with self.assertRaisesRegex(ValueError, 'original Tool exposure'):
            engine.validate_resume(damaged, decisions={'tool:1:call_1': True})

    async def test_technical_deny_still_applies_after_approval(self):
        permission = {'allowed': True}
        runtime = ToolRuntime(authorize=lambda *_: permission['allowed'])
        _, session, _, _ = await self.setup([[chunk(calls=[call('{}', name='first')], finish='tool_calls')]],
            selector=Selector({1: ['first']}), approval=('first',), runtime=runtime)
        paused = await self.submit(session)
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('approve'))
        permission['allowed'] = False
        resumed = (await (await session.run.resume(paused.id, agent_id='worker')).wait(timeout=15))[0]
        self.assertEqual(resumed.data.status, 'failed')
        self.assertEqual(self.effects, [])

    async def test_selector_failure_and_cancellation_do_not_fall_back_or_execute(self):
        class Failing(Selector):
            async def select_tools(self, request):
                raise RuntimeError('selection failed')
        _, session, model, _ = await self.setup([], selector=Failing({}))
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'failed')
        self.assertIn('selection failed', run.data.error)
        self.assertEqual(model.requests, [])
        entered, closed = asyncio.Event(), asyncio.Event()
        class Waiting(Selector):
            async def select_tools(self, request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        _, session, model, _ = await self.setup([], selector=Waiting({}))
        handle = await session.run.submit('go', recipients=['worker'])
        await asyncio.wait_for(entered.wait(), 10)
        await session.run.interrupt()
        run = (await handle.wait(timeout=15))[0]
        self.assertEqual(run.data.status, 'interrupted', run.data.error)
        self.assertTrue(closed.is_set())
        self.assertEqual(model.requests, [])
        self.assertEqual(self.effects, [])

    async def test_concurrent_sessions_keep_choices_local(self):
        entered = asyncio.Event()
        class Concurrent(Selector):
            async def select_tools(self, request):
                self.requests.append(request)
                if len(self.requests) == 2:
                    entered.set()
                await entered.wait()
                return [request.messages[-1].text]
        selector = Concurrent({})
        _, session, model, _ = await self.setup([[chunk('done', finish='stop')]] * 2, selector=selector)
        other = await session.project.sessions.acreate('two', agent_ids=['worker'])
        first, second = await asyncio.gather(self.submit(session, 'first'), self.submit(other, 'second'))
        self.assertEqual((first.data.status, second.data.status), ('completed', 'completed'))
        for request in model.requests:
            self.assertEqual(self.names(request), [request['messages'][-1]['content']])

    async def test_provider_retry_keeps_selection_and_does_not_repeat_selector(self):
        from tests.llm.configuration_fixtures import configure_engine
        selector = Selector({1: ['first']})
        _, session, model, _ = await self.setup([[ConnectionError('temporary')],
            [chunk('done', finish='stop')]], selector=selector)
        await configure_engine(session.project, 'loop', provider={'max_attempts': 2,
            'delay_seconds': 0, 'max_delay_seconds': 0})
        run = await self.submit(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual([self.names(r) for r in model.requests], [['first'], ['first']])
        self.assertEqual(len(selector.requests), 1)

    async def test_nested_agent_uses_its_own_exposure_without_narrowing_parent_authority(self):
        child_selector = Selector({1: ['first'], 2: []})
        app, session, child, _ = await self.setup([
            [chunk(calls=[call('{}', name='first')], finish='tool_calls')],
            [chunk('child done', finish='stop')]], selector=child_selector, approval=('first',))
        parent = ScriptedCompletion([chunk(calls=[call('{"agent_id":"worker","input":{"task":"go"}}',
            name='agent_run')], finish='tool_calls')], [chunk('parent done', finish='stop')])
        parent_selector = Selector({1: ['agent_run'], 2: []})
        app.engines.register('parent', LoopEngine(tool_selector=parent_selector,
            completion_adapter=LiteLLMCompletionAdapter(completion_fn=parent)))
        await session.project.agents.acreate({'engine': 'parent', 'completion': {'model': 'test/model'},
            'tools': ['agent_run', 'first', 'second']}, identifier='parent')
        await session.asave(agent_ids=['parent', 'worker'])
        paused = (await (await session.run.submit('go', recipients=['parent'])).wait(timeout=15))[0]
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(self.effects, [])
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('approve'))
        resumed = (await (await session.run.resume(paused.id, agent_id='parent')).wait(timeout=15))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [('first', {})])
        self.assertEqual([self.names(r) for r in parent.requests], [['agent_run'], []])
        self.assertEqual([self.names(r) for r in child.requests], [['first'], []])
        self.assertEqual([r.iteration for r in parent_selector.requests], [1, 2])
        self.assertEqual([r.iteration for r in child_selector.requests], [1, 2])

    async def test_reopen_resumes_saved_exposure_without_reselecting_waiting_invocation(self):
        selector = Selector({1: ['first'], 2: []})
        app, session, model, engine = await self.setup([
            [chunk(calls=[call('{}', name='first')], finish='tool_calls')],
            [chunk('done', finish='stop')]], selector=selector, approval=('first',))
        paused = await self.submit(session)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        project_id, session_id = session.project.id, session.id
        components = [app.project_manager.components.get('tools')]
        await app.shutdown()
        selector.choices = {2: []}
        reopened = LargeLanguageModel(app.workspace, engines={'loop': engine}, components=components)
        self.addAsyncCleanup(reopened.shutdown)
        project = await reopened.projects.aload(project_id)
        session = await project.sessions.aload(session_id)
        paused = await session.run.aload(paused.id)
        request, = await paused.ainteractions(pending_only=True)
        await paused.arespond(request.respond('approve'))
        resumed = (await (await session.run.resume(paused.id, agent_id='worker')).wait(timeout=15))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(self.effects, [('first', {})])
        self.assertEqual(len(model.requests), 2)
        self.assertEqual([r.iteration for r in selector.requests], [1, 2])
