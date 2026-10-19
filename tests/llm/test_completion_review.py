"""F: final-candidate authority, durable decisions, budgets and C12 isolation."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from llm.components.processing import (CompletionSession, CompletionPipeline, CompletionReview,
    CompletionReviewObservation, ReviewAction)
from llm.core.model_protocol import ModelMessage, ModelResponse
from llm.engines.loop import LoopEngine
from llm.engines.loop.review import LoopReview
from llm.llm import LargeLanguageModel, BackendServices
from llm.components.tools import Tool, ToolContract, ToolRegistry
from tests.llm.support.common_provider import CommonProvider
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_completion_processing import Processor, Feature


def answer(text, *, usage=2):
    return [{'fragment': text}, {'sealed': {'say': text, **({'spent': {'total_tokens': usage}} if usage is not None else {})}}]


def reviewer(factory, name='check', revision='v1', priority=0):
    processor = Processor(name, factory, priority)
    if revision is not None:
        processor.review_revision = revision
    return processor


class ReviewContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_default_and_multiple_decisions_are_detached_and_strict(self):
        observation = CompletionReviewObservation(1, (ModelMessage('user', 'original', opaque={'x': 1}),),
            ModelResponse(ModelMessage('assistant', 'candidate')), (ModelMessage('user', 'original'),))
        context = SimpleNamespace(run=SimpleNamespace(input_message_id='current'))
        async with CompletionPipeline([], context) as pipeline:
            self.assertEqual(await pipeline.review_final(observation), [])
        async with CompletionPipeline([reviewer(lambda _: CompletionSession())], context) as pipeline:
            self.assertEqual((await pipeline.review_final(observation))[0]['action'], 'accept')
        seen = []
        class First(CompletionSession):
            async def review_final(self, value):
                value.messages[0].opaque['x'] = 999
                return CompletionReview(ReviewAction.CONTINUE, 'first')
        class Second(CompletionSession):
            async def review_final(self, value):
                seen.append(value.messages[0].opaque['x'])
                return CompletionReview(ReviewAction.BLOCK, 'second')
        async with CompletionPipeline([reviewer(lambda _: Second(), 'b'), reviewer(lambda _: First(), 'a')], context) as pipeline:
            decisions = await pipeline.review_final(observation)
        self.assertEqual(seen, [1])
        self.assertEqual(LoopReview.decision({}, decisions)['action'], 'block')
        self.assertEqual([d['name'] for d in decisions], ['a', 'b'])
        self.assertEqual(observation.messages[0].opaque['x'], 1)
        with self.assertRaises(TypeError):
            CompletionReview('continue')
        async with CompletionPipeline([reviewer(lambda _: First(), 'a'), reviewer(lambda _: Second(), 'b')], context) as pipeline:
            with self.assertRaisesRegex(ValueError, 'feedback'):
                await pipeline.review_final(observation, {'max_feedback_chars': 5})
        with self.assertRaisesRegex(ValueError, 'review_revision'):
            async with CompletionPipeline([reviewer(lambda _: First(), revision=None)], context):
                pass


class CompletionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, provider, processors=(), *, config=None, components=(), services=None, on_event=None, engines=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        features = [Feature('review_' + p.name, p) for p in processors]
        self.app = LargeLanguageModel(self.root, engines=engines or {'loop': LoopEngine(completion_adapter=provider)},
            components=[*features, *components], services=services, on_event=on_event)
        self.addAsyncCleanup(self.app.shutdown)
        config = deepcopy(config or {})
        config.setdefault('parameters', {}).setdefault('engines', {}).setdefault('loop', {}).setdefault('config', {}).setdefault('completion', {'model': 'fake'})
        self.project = await self.app.projects.acreate(components=[c.name for c in (*features, *components)], config=config)
        self.session = await self.project.sessions.acreate()
        await self.project.agents.acreate({'engine': 'loop'}, identifier='writer')
        await self.session.agents.aattach('writer')
        return self.session

    async def execute(self, text='original'):
        return await (await self.session.run.submit(text, agent_id='writer')).wait(timeout=15)

    async def test_continue_then_accept_buffers_candidates_and_preserves_transcript_and_usage(self):
        trace, events = [], []
        class Review(CompletionSession):
            async def after_completion(self, value):
                trace.append(('after', value.iteration))
                if False: yield
            async def review_final(self, value):
                trace.append(('review', value.iteration))
                return CompletionReview(ReviewAction.CONTINUE if value.iteration < 3 else ReviewAction.ACCEPT,
                                        'check-' + str(value.iteration))
            async def finish(self, value):
                trace.append(('finish', value.iteration))
                if False: yield
        async def observe(run, event):
            events.append(event)
        provider = CommonProvider(answer('rejected-one'), answer('rejected-two'), answer('accepted'))
        await self.make(provider, [reviewer(lambda _: Review())], on_event=observe)
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual((await run.aresponse()).content, 'accepted')
        self.assertEqual(trace, [('after', 1), ('review', 1), ('after', 2), ('review', 2), ('after', 3), ('review', 3), ('finish', 3)])
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(len(run.data.metadata['completions']), 3)
        self.assertEqual([m.content for m in await self.session.aconversation() if m.role == 'user'], ['original'])
        for request in provider.requests:
            self.assertEqual(request.messages[0].text, 'original')
            request.validate_exchanges()
        self.assertIn('untrusted reference data', provider.requests[1].messages[-1].text)
        self.assertIn('check-1', provider.requests[1].messages[-1].text)
        self.assertTrue(all(e.delta.visibility == 'internal' for e in events if e.delta is not None))
        self.assertEqual([e.output.text for e in events if e.type == 'output' and e.output.visibility == 'user'], ['accepted'])
        checkpoint = await run.acheckpoint('loop')
        LoopReview.validate(checkpoint)
        self.assertEqual([checkpoint['records'][f'review:{i}']['action'] for i in (1, 2, 3)], ['continue', 'continue', 'accept'])
        self.assertTrue(all(s.output.visibility == 'internal' for s in await run.steps.alist() if s.kind == 'llm'))

    async def test_no_review_streams_before_completion_and_has_legacy_checkpoint_shape(self):
        visible, release = asyncio.Event(), asyncio.Event()
        class Held(CommonProvider):
            async def stream(self, request, **kwargs):
                async for envelope in super().stream(request, **kwargs):
                    yield envelope
                    if 'fragment' in envelope:
                        await release.wait()
        async def observe(run, event):
            if event.delta is not None and event.delta.visibility == 'user':
                visible.set()
        provider = Held(answer('streamed'))
        await self.make(provider, [reviewer(lambda _: CompletionSession(), revision=None)], on_event=observe)
        self.addCleanup(release.set)
        request = await self.session.run.submit('original', agent_id='writer')
        await asyncio.wait_for(visible.wait(), 10)
        self.assertEqual((await request.aget_run()).data.status, 'running')
        release.set()
        run = await request.wait(timeout=10)
        self.assertEqual((await run.aresponse()).content, 'streamed')
        checkpoint = await run.acheckpoint('loop')
        self.assertNotIn('review', checkpoint['header'])
        self.assertFalse(any(k.startswith('review') for k in checkpoint['records']))

    async def test_block_invalid_result_exception_and_timeout_never_publish_candidate(self):
        for mode in ('block', 'invalid', 'exception', 'timeout', 'oversized'):
            with self.subTest(mode=mode):
                closed = []
                class Review(CompletionSession):
                    async def review_final(self, value):
                        if mode == 'invalid': return {'action': 'accept'}
                        if mode == 'exception': raise ValueError('review failure')
                        if mode == 'timeout': await asyncio.Event().wait()
                        return CompletionReview(ReviewAction.BLOCK, 'x' * (20 if mode == 'oversized' else 1))
                    async def finish(self, value):
                        raise AssertionError('Rejected candidate reached finish')
                        yield
                    async def aclose(self, error): closed.append(type(error).__name__)
                provider = CommonProvider(answer('private candidate'))
                await self.make(provider, [reviewer(lambda _: Review())], config={'parameters': {'engines': {'loop': {'policy': {
                    'final_review': {'hook_timeout': .02, 'max_feedback_chars': 10}}}}}})
                run = await self.execute()
                self.assertEqual(run.data.status, 'failed', run.data.error)
                self.assertEqual((await run.aresponse()).content, '')
                self.assertEqual(len(provider.requests), 1)
                self.assertTrue(closed)
                if mode in ('block', 'timeout'):
                    self.assertEqual(run.data.error_code, 'completion_review_' + ('blocked' if mode == 'block' else 'timeout'))
                await self.app.shutdown()

    async def test_cancellation_closes_reviewer_and_leaves_no_candidate_response(self):
        entered, closed = asyncio.Event(), []
        class Review(CompletionSession):
            async def review_final(self, value):
                entered.set()
                await asyncio.Event().wait()
            async def aclose(self, error): closed.append(type(error).__name__)
        provider = CommonProvider(answer('private'))
        await self.make(provider, [reviewer(lambda _: Review())])
        request = await self.session.run.submit('original', agent_id='writer')
        await asyncio.wait_for(entered.wait(), 10)
        current = await request.aget_run()
        self.assertTrue(await self.session.run.cancel(current.id))
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, 'interrupted')
        self.assertEqual((await run.aresponse()).content, '')
        self.assertEqual(closed, ['CancelledError'])
        self.assertEqual((await run.acheckpoint('loop'))['records']['review_attempt:1']['attempt'], 1)

    async def test_continuation_iteration_call_and_token_limits_do_not_reset(self):
        class Review(CompletionSession):
            async def review_final(self, value): return CompletionReview(ReviewAction.CONTINUE, 'again')
        cases = [({'parameters': {'engines': {'loop': {'policy': {'final_review': {'max_continuations': 1}}}}}}, 2, 'completion_review_limit'),
                 ({'parameters': {'engines': {'loop': {'policy': {'max_iterations': 1}}}}}, 1, None),
                 ({'policies': {'usage': {'max_calls': 1}}}, 1, 'usage_limit'),
                 ({'policies': {'usage': {'max_tokens': 6, 'counter': 'fixed'}}}, 1, 'usage_limit')]
        for config, count, code in cases:
            with self.subTest(config=config):
                provider = CommonProvider(answer('first'), answer('second'), answer('third'))
                await self.make(provider, [reviewer(lambda _: Review())], config=config,
                    services=BackendServices(token_counters={'fixed': lambda _: 5}))
                run = await self.execute()
                self.assertEqual(run.data.status, 'failed', run.data.error)
                self.assertEqual(len(provider.requests), count)
                self.assertEqual((await run.aresponse()).content, '')
                if code: self.assertEqual(run.data.error_code, code)
                await self.app.shutdown()

    async def test_resume_reuses_candidate_and_bounds_reviewer_attempts(self):
        calls = []
        class Review(CompletionSession):
            async def review_final(self, value):
                calls.append(value.to_dict())
                raise ValueError('pure review unavailable')
        provider = CommonProvider(answer('candidate'))
        await self.make(provider, [reviewer(lambda _: Review())], config={'parameters': {'engines': {'loop': {'policy': {
            'final_review': {'max_attempts': 2}}}}}})
        run = await self.execute()
        for expected in (2, 2):
            run = await (await self.session.run.resume(run.id)).wait(timeout=10)
            self.assertEqual(run.data.status, 'failed')
            self.assertEqual(len(calls), expected)
        self.assertEqual(run.data.error_code, 'completion_review_attempts')
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual((await run.aresponse()).content, '')

    async def test_resume_preserves_physical_call_and_unknown_token_reservations(self):
        for token_limit in (False, True):
            with self.subTest(token_limit=token_limit):
                attempts = []
                class Review(CompletionSession):
                    async def review_final(self, value):
                        attempts.append(value.iteration)
                        if len(attempts) == 1: raise ValueError('transient pure review failure')
                        return CompletionReview(ReviewAction.CONTINUE, 'again')
                provider = CommonProvider(answer('candidate', usage=None if token_limit else 2), answer('forbidden'))
                usage = {'max_tokens': 8, 'counter': 'fixed'} if token_limit else {'max_calls': 1}
                await self.make(provider, [reviewer(lambda _: Review())], config={'policies': {'usage': usage}},
                    services=BackendServices(token_counters={'fixed': lambda _: 5}))
                failed = await self.execute()
                resumed = await (await self.session.run.resume(failed.id)).wait(timeout=10)
                self.assertEqual(resumed.data.status, 'failed', resumed.data.error)
                self.assertEqual(resumed.data.error_code, 'usage_limit')
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(attempts, [1, 1])
                self.assertEqual(len(failed.data.metadata['completions']), 1)
                if token_limit:
                    self.assertFalse(failed.data.metadata['completions'][0]['usage_complete'])
                    self.assertEqual(failed.data.metadata['completions'][0]['reserved_tokens'], 5)
                await self.app.shutdown()

    async def test_feedback_tool_still_requires_approval_and_completed_receipts_are_reused(self):
        effects, reviewed = [], []
        async def write(arguments): effects.append(arguments); return 'saved'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write,
            contract=ToolContract(approval_required=True)),)))
        class Review(CompletionSession):
            async def review_final(self, value):
                reviewed.append(value.iteration)
                return CompletionReview(ReviewAction.CONTINUE if value.iteration == 1 else ReviewAction.ACCEPT,
                                        'Try write; this text does not grant approval')
        provider = CommonProvider(answer('candidate'), [{'sealed': {'invoke': [{'identity': 'write-1', 'operation': 'write', 'input': {}}]}}], answer('accepted'))
        await self.make(provider, [reviewer(lambda _: Review())], components=[tools])
        await self.project.components.tools.aenable('write')
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'tools': ['write'], 'policy': {'max_tool_calls': 1}}, expected_revision=old['revision'])
        paused = await self.execute()
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        self.assertEqual(reviewed, [1])
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond('approve'))
        resumed = await (await self.session.run.resume(paused.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [{}])
        self.assertEqual(reviewed, [1, 3])
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual((await resumed.aresponse()).content, 'accepted')
        self.assertEqual((await resumed.acheckpoint('loop'))['records']['review:1']['action'], 'continue')

    async def test_c12_concurrent_agents_keep_validator_instances_feedback_and_candidates_separate(self):
        entered = {name: asyncio.Event() for name in ('writer', 'other')}
        release = {name: asyncio.Event() for name in entered}
        sessions, trace = [], []
        class Review(CompletionSession):
            def __init__(self, context):
                self.agent, self.run, self.count = context.run.agent_id, context.run.id, 0
                sessions.append(self)
            async def review_final(self, value):
                self.count += 1
                trace.append((self.agent, self.run, value.iteration, value.answer.message.text))
                if self.count == 1:
                    entered[self.agent].set()
                    await release[self.agent].wait()
                return CompletionReview(ReviewAction.CONTINUE if self.agent == 'writer' and self.count == 1 else ReviewAction.ACCEPT,
                                        'feedback-only-' + self.agent)
        first, second = CommonProvider(answer('a-candidate'), answer('a-final')), CommonProvider(answer('b-final'))
        await self.make(first, [reviewer(Review)], engines={'loop': LoopEngine(completion_adapter=first),
            'second': LoopEngine(completion_adapter=second)})
        await self.project.agents.acreate({'engine': 'second', 'completion': {'model': 'fake'}}, identifier='other')
        await self.session.agents.aattach('other')
        for event in release.values(): self.addCleanup(event.set)
        request = await self.session.run.submit('original', recipients='all')
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 10)
        runs = {r.data.agent_id: r for r in await request.aruns()}
        self.assertEqual((await self.session.aget_data()).active_run_ids, {r.id for r in runs.values()})
        self.assertEqual([(await r.aresponse()).content for r in runs.values()], ['', ''])
        release['other'].set()
        manager = self.app._manager(self.session._snapshot)
        other = manager._active.executions.get(runs['other'].id)
        if other is not None: await asyncio.wait_for(other.finished.wait(), 10)
        self.assertEqual((await self.session.aget_data()).active_run_ids, {runs['writer'].id})
        self.assertEqual((await runs['other'].aresponse()).content, 'b-final')
        release['writer'].set()
        completed = await request.wait_all(timeout=10)
        terminal = [await r.aget_data() for r in completed]
        self.assertTrue(all(r.status == 'completed' for r in terminal))
        self.assertEqual({(s.agent, s.run, s.count) for s in sessions},
                         {('writer', runs['writer'].id, 2), ('other', runs['other'].id, 1)})
        self.assertIn('feedback-only-writer', first.requests[1].messages[-1].text)
        self.assertNotIn('feedback-only-other', first.requests[1].messages[-1].text)
        self.assertEqual(len(second.requests), 1)
        self.assertEqual((await runs['writer'].aresponse()).content, 'a-final')
        for agent, run in runs.items():
            checkpoint = await run.acheckpoint('loop')
            self.assertEqual(checkpoint['records']['review:1']['decisions'][0]['feedback'], 'feedback-only-' + agent)
            self.assertTrue(all(s.run_id == run.id for s in await run.steps.alist()))
        print('F_C12=' + json.dumps({'request': request.id, 'trace': trace, 'runs': {a: r.id for a, r in runs.items()}}))

    async def test_agent_selection_narrows_parent_and_nested_graph_review_has_step_scope(self):
        from llm.components.workflows import WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        from tests.llm.test_agent_workflow import agent_graph
        reviewed = []
        class Review(CompletionSession):
            def __init__(self, context, name): self.context, self.name = context, name
            async def review_final(self, value):
                reviewed.append((self.name, self.context.run.id, self.context.output_step_id))
                return CompletionReview()
        provider = CommonProvider(answer('nested accepted'), answer('unreviewed'))
        loop = LoopEngine(completion_adapter=provider)
        graph = GraphEngine(handlers={'agent': AgentNode(engines={'loop': loop})})
        await self.make(provider, [reviewer(lambda c: Review(c, 'a'), 'a'), reviewer(lambda c: Review(c, 'b'), 'b')],
            engines={'loop': loop, 'graph': graph}, components=[WorkflowComponent()])
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'completion_processors': ['a']}, expected_revision=old['revision'])
        await self.project.components.workflows.acreate(agent_graph(), identifier='flow')
        await self.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'},
            'completion_processors': ['a']}, identifier='coordinator')
        await self.session.agents.aattach('coordinator')
        run = await (await self.session.run.submit('original', agent_id='coordinator')).wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0][:2], ('a', run.id))
        self.assertIsNotNone(reviewed[0][2])
        self.assertEqual(len(await self.session.run.alist()), 1)
        checkpoint = await run.acheckpoint('graph')
        receipts = [v for v in checkpoint['records'].values() if v.get('local_key') == 'review:1']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['payload']['action'], 'accept')
        self.assertEqual(receipts[0]['payload']['decisions'][0]['name'], 'a')
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'completion_processors': ['b']}, expected_revision=old['revision'])
        failed = await (await self.session.run.submit('cannot widen', agent_id='coordinator')).wait(timeout=10)
        self.assertEqual(failed.data.status, 'failed')
        self.assertIn('parent selection', failed.data.error)
        self.assertEqual(len(provider.requests), 1)
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'completion_processors': []}, expected_revision=old['revision'])
        ordinary = await self.execute('disabled explicitly')
        self.assertEqual(ordinary.data.status, 'completed', ordinary.data.error)
        self.assertNotIn('review', (await ordinary.acheckpoint('loop'))['header'])
        self.assertEqual(len(reviewed), 1)

    async def test_revision_changes_and_corrupt_decision_receipts_refuse_resume(self):
        class Review(CompletionSession):
            async def review_final(self, value): return CompletionReview()
            async def finish(self, value):
                raise ValueError('stop after durable decision')
                yield
        provider = CommonProvider(answer('candidate'))
        processor = reviewer(lambda _: Review())
        await self.make(provider, [processor])
        run = await self.execute()
        self.assertEqual(run.data.status, 'failed')
        checkpoint = await run.acheckpoint('loop')
        LoopReview.validate(checkpoint)
        mutations = [lambda c: c['records']['review:1'].update(candidate='0' * 64),
                     lambda c: c['records']['review:1'].update(action='continue'),
                     lambda c: c['records']['review:1']['decisions'][0].update(revision='changed'),
                     lambda c: c['records'].pop('review_attempt:1'),
                     lambda c: c['header'].pop('review'),
                     lambda c: c['records']['review:1'].update(extra='authority'),
                     lambda c: c['records']['review_attempt:1'].update(attempt=True)]
        for mutate in mutations:
            bad = deepcopy(checkpoint); mutate(bad)
            with self.assertRaises((ValueError, TypeError)):
                LoopReview.validate(bad)
        processor.review_revision = 'v2'
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.session.run.resume(run.id)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(await self.session.run.alist()), 1)

    async def test_review_checkpoint_excludes_invocation_credentials(self):
        class PrivateProvider(CommonProvider):
            def describe_config(self):
                value = super().describe_config()
                value['properties']['api_key'] = {'type': 'string'}
                return value
        snapshots = []
        class Review(CompletionSession):
            async def review_final(self, value):
                snapshots.append(value.to_dict())
                return CompletionReview()
        provider = PrivateProvider(answer('accepted'))
        secret = 'private-invocation-credential'
        await self.make(provider, [reviewer(lambda _: Review())], config={'parameters': {'engines': {'loop': {
            'config': {'completion': {'model': 'fake', 'api_key': secret}}}}}})
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(provider.requests[0].parameters['api_key'], secret)
        self.assertNotIn(secret, json.dumps(await run.acheckpoint('loop')))
        self.assertNotIn(secret, json.dumps(snapshots))
        self.assertNotIn(secret, json.dumps(run.data.metadata))

    async def test_original_review_deadline_survives_explicit_resume(self):
        from datetime import timedelta
        from llm.engines.loop import review as review_module
        calls = []
        class Review(CompletionSession):
            async def review_final(self, value):
                calls.append(value.iteration)
                raise ValueError('temporary pure validator failure')
        provider = CommonProvider(answer('candidate'))
        await self.make(provider, [reviewer(lambda _: Review())], config={'policies': {'run': {'timeout_seconds': 60}}})
        failed = await self.execute()
        checkpoint = await failed.acheckpoint('loop')
        self.assertIsNotNone(checkpoint['header']['review']['deadline'])
        future = review_module.clock() + timedelta(seconds=120)
        with patch.object(review_module, 'clock', return_value=future):
            resumed = await (await self.session.run.resume(failed.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'failed', resumed.data.error)
        self.assertEqual(resumed.data.error_code, 'run_timeout')
        self.assertEqual(calls, [1])
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual((await resumed.aresponse()).content, '')

    async def test_memory_goal_processing_preserves_tool_pairs_through_review_continuation(self):
        from llm.components.memory import MemoryComponent
        from llm.components.goals import GoalComponent
        from tests.llm.configuration_fixtures import memory_settings
        from tests.llm.test_goals import goal
        observed, effects = [], []
        async def lookup(arguments): effects.append(1); return 'x' * 200
        tools = RuntimeTools(ToolRegistry((Tool('lookup', 'lookup', {'type': 'object'}, lookup),)))
        class Review(CompletionSession):
            async def review_final(self, value):
                observed.append(value)
                return CompletionReview(ReviewAction.CONTINUE if value.iteration == 2 else ReviewAction.ACCEPT, 'check again')
        provider = CommonProvider([{'sealed': {'invoke': [{'identity': 'lookup-1', 'operation': 'lookup', 'input': {}}]}}],
                                  answer('candidate'), answer('accepted'))
        await self.make(provider, [reviewer(lambda _: Review())], components=[tools, MemoryComponent(), GoalComponent()])
        await self.project.components.tools.aenable('lookup')
        await self.project.components.memory.aconfigure(memory_settings({'processing': {
            'priority': 100, 'compress_tools': True, 'tool_result_chars': 20}}))
        await self.project.components.goals.acreate(goal(), identifier='goal')
        await self.project.components.goals.aconfigure({'config': {'priority': 10}, 'policy': {'inject': True}})
        before = await self.project.components.goals.asnapshot('goal')
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'tools': ['lookup']}, expected_revision=old['revision'])
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(effects, [1])
        self.assertEqual([o.iteration for o in observed], [2, 3])
        self.assertEqual((await run.aresponse()).content, 'accepted')
        self.assertEqual(await self.project.components.goals.asnapshot('goal'), before)
        for request in provider.requests:
            request.validate_exchanges()
            current = next(m for m in request.messages if m.role == 'user')
            self.assertTrue(current.text.startswith('original'))
            self.assertIn('Finish safely', current.text)
        for observation in observed:
            raw = next(m.result for m in observation.original_messages if m.result)
            prepared = next(m.result for m in observation.messages if m.result)
            self.assertEqual(raw.content, 'x' * 200)
            self.assertEqual(prepared.call_id, raw.call_id)
            self.assertIn('"truncated": true', prepared.content)
        tool_step, = [s for s in await run.steps.alist() if s.kind == 'tool']
        self.assertEqual(tool_step.output.data, 'x' * 200)

    async def test_nested_graph_resume_reuses_committed_review_without_model_replay(self):
        from llm.components.workflows import WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.engines.graph.agent import AgentNode
        from tests.llm.test_agent_workflow import agent_graph
        reviewed, finished = [], []
        class Review(CompletionSession):
            async def review_final(self, value):
                reviewed.append(value.to_dict())
                return CompletionReview()
            async def finish(self, value):
                finished.append(value.answer.message.text)
                if len(finished) == 1: raise ValueError('interrupted application finish')
                if False: yield
        provider = CommonProvider(answer('nested accepted'))
        loop = LoopEngine(completion_adapter=provider)
        await self.make(provider, [reviewer(lambda _: Review())], engines={'loop': loop,
            'graph': GraphEngine(handlers={'agent': AgentNode(engines={'loop': loop})})}, components=[WorkflowComponent()])
        await self.project.components.workflows.acreate(agent_graph(), identifier='flow')
        await self.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='coordinator')
        await self.session.agents.aattach('coordinator')
        failed = await (await self.session.run.submit('original', agent_id='coordinator')).wait(timeout=10)
        self.assertEqual(failed.data.status, 'failed')
        resumed = await (await self.session.run.resume(failed.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(finished, ['nested accepted', 'nested accepted'])
        self.assertEqual(resumed.data.metadata['resume']['run_id'], failed.id)
        receipts = [v for v in (await resumed.acheckpoint('graph'))['records'].values() if v.get('local_key') == 'review:1']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['payload']['action'], 'accept')

    async def test_live_steering_during_review_survives_accept_and_continue(self):
        for reject in (False, True):
            with self.subTest(reject=reject):
                entered, release = asyncio.Event(), asyncio.Event()
                class Review(CompletionSession):
                    async def review_final(self, value):
                        if value.iteration == 1:
                            entered.set()
                            await release.wait()
                        return CompletionReview(ReviewAction.CONTINUE if reject and value.iteration == 1 else ReviewAction.ACCEPT, 'review feedback')
                provider = CommonProvider(answer('candidate'), answer('revised'))
                await self.make(provider, [reviewer(lambda _: Review())])
                self.addCleanup(release.set)
                request = await self.session.run.submit('original', agent_id='writer')
                await asyncio.wait_for(entered.wait(), 10)
                current = await request.aget_run()
                instruction = await self.session.run.steer(current.id, 'new requirement')
                release.set()
                run = await request.wait(timeout=10)
                self.assertEqual(run.data.status, 'completed', run.data.error)
                self.assertEqual(run.id, current.id)
                self.assertEqual((await run.aresponse()).content, 'revised')
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(provider.requests[1].messages[-1].text, 'new requirement')
                self.assertEqual(provider.requests[1].messages[0].text, 'original')
                self.assertEqual(any('review feedback' in m.text for m in provider.requests[1].messages), reject)
                applied, = await run.ainstructions()
                self.assertEqual(applied.id, instruction.id)
                self.assertEqual(applied.status, 'applied')
                self.assertEqual(applied.applications[0]['iteration'], 2)
                await self.app.shutdown()

    async def test_resumed_finish_preserves_memory_prepare_boundary_and_auxiliary_receipts(self):
        from llm.components.memory import MemoryComponent
        from llm.components.prompts import PromptComponent
        from tests.llm.configuration_fixtures import memory_settings, memory_processing
        from tests.llm.test_memory_processing import Auxiliary
        auxiliary, reviewed, finished = Auxiliary(), [], []
        class Review(CompletionSession):
            async def review_final(self, value):
                reviewed.append(value.iteration)
                return CompletionReview()
            async def finish(self, value):
                finished.append(value.iteration)
                raise ValueError('application failure after auxiliary extraction')
                yield
        provider = CommonProvider(answer('accepted'))
        await self.make(provider, [reviewer(lambda _: Review(), priority=10000)],
            components=[MemoryComponent(completion_fn=auxiliary), PromptComponent()],
            config={'policies': {'usage': {'max_calls': 2}}})
        await self.project.components.prompts.acreate({'messages': [{'role': 'system', 'content': 'Extract facts.'}]}, identifier='extract')
        await self.project.components.memory.aconfigure(memory_settings({'processing': memory_processing({
            'recall': False, 'extract': True, 'failure_mode': 'raise', 'extract_prompt_id': 'extract',
            'completion': {'model': 'test/aux'}})}))
        failed = await self.execute()
        self.assertEqual(failed.data.status, 'failed')
        self.assertEqual(len(auxiliary.requests), 1)
        self.assertEqual(finished, [1])
        resumed = await (await self.session.run.resume(failed.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'failed')
        # Saved responses do not rerun prepare. Memory's existing lazy prepare
        # state therefore does not extract again; generic finish still runs.
        self.assertEqual(resumed.data.error_code, 'engine_failed', resumed.data.error)
        self.assertIn('application failure after auxiliary extraction', resumed.data.error)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(len(auxiliary.requests), 1)
        self.assertEqual(reviewed, [1])
        self.assertEqual(finished, [1, 1])
        self.assertEqual(len(failed.data.metadata['completions']), 2)
        self.assertEqual(resumed.data.metadata.get('completions', []), [])
        self.assertEqual((await self.project.amodel_usage())['call_count'], 2)

    async def test_resumed_component_call_counts_ancestor_model_and_component_receipts(self):
        from tests.llm.test_operations_schema import OperationsSchemaTests
        from tests.llm.configuration_fixtures import rag_settings
        embeddings, reviewed, attempted = [], [], []
        async def embedding(**request):
            embeddings.append(request['input'])
            return {'data': [{'index': i, 'embedding': [1.0, 0.5]} for i, _ in enumerate(request['input'])],
                    'usage': {'total_tokens': 3}}
        owner = self
        class Review(CompletionSession):
            def __init__(self, context): self.run = context.run.id
            async def review_final(self, value):
                reviewed.append(value.iteration)
                return CompletionReview()
            async def finish(self, value):
                attempted.append(self.run)
                await owner.project.components.rag.asearch('reference for ' + self.run)
                raise ValueError('finish failure after component call')
                yield
        provider = CommonProvider(answer('accepted'))
        await self.make(provider, [reviewer(Review)], components=[OperationsSchemaTests.rag(self, embed=embedding)],
            config={'policies': {'usage': {'max_calls': 2}}, 'parameters': {'components': {'rag': rag_settings()}}})
        await self.project.components.rag.aadd_document(title='Guide', content='Local scripting reference.')
        failed = await self.execute()
        self.assertEqual(failed.data.status, 'failed')
        self.assertIn('finish failure', failed.data.error)
        before = len(embeddings)
        owned = [r for r in await self.project.components.rag.amodel_usage() if r.get('run_id') == failed.id]
        self.assertEqual(len(owned), 1)
        resumed = await (await self.session.run.resume(failed.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'failed')
        self.assertEqual(resumed.data.error_code, 'usage_limit', resumed.data.error)
        self.assertEqual(len(embeddings), before)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(reviewed, [1])
        self.assertEqual(attempted, [failed.id, resumed.id])
        self.assertEqual([r for r in await self.project.components.rag.amodel_usage() if r.get('run_id') == resumed.id], [])

    async def test_agent_run_reviews_child_and_parent_in_separate_checkpoint_scopes(self):
        from llm.components.agents import AgentComponent
        trace = []
        class Review(CompletionSession):
            def __init__(self, context): self.context = context
            async def review_final(self, value):
                trace.append((self.context.run.id, self.context.output_step_id, value.answer.message.text))
                return CompletionReview(ReviewAction.CONTINUE if value.answer.message.text == 'child candidate' else ReviewAction.ACCEPT,
                                        'child reference only')
        parent = CommonProvider([{'sealed': {'invoke': [{'identity': 'delegate-1', 'operation': 'agent_run',
            'input': {'agent_id': 'child', 'input': {'task': 'work'}}}]}}], answer('parent accepted'))
        child = CommonProvider(answer('child candidate'), answer('child accepted'))
        engines = {'loop': LoopEngine(completion_adapter=parent), 'child_engine': LoopEngine(completion_adapter=child)}
        await self.make(parent, [reviewer(lambda c: Review(c))], engines=engines, components=[AgentComponent(engines=engines)])
        await self.project.agents.acreate({'engine': 'child_engine', 'completion': {'model': 'fake'}, 'tools': [],
            'completion_processors': ['check']}, identifier='child')
        old = await self.project.agents.asnapshot('writer')
        await self.project.agents.arevise('writer', {'engine': 'loop', 'tools': ['agent_run']}, expected_revision=old['revision'])
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(len(await self.session.run.alist()), 1)
        self.assertEqual((await run.aresponse()).content, 'parent accepted')
        self.assertEqual(len(parent.requests), 2)
        self.assertEqual(len(child.requests), 2)
        self.assertEqual(len(run.data.metadata['completions']), 4)
        self.assertEqual({r for r, _, _ in trace}, {run.id})
        child_steps = {s for _, s, text in trace if text.startswith('child')}
        self.assertEqual(len(child_steps), 1)
        self.assertNotIn(None, child_steps)
        self.assertEqual(trace[-1], (run.id, None, 'parent accepted'))
        checkpoint = await run.acheckpoint('loop')
        self.assertEqual(checkpoint['records']['review:2']['action'], 'accept')
        nested = [r for r in checkpoint['records'].values() if r.get('local_key', '').startswith('review:')]
        self.assertEqual(len(nested), 2)
        self.assertEqual({r['local_key']: r['payload']['action'] for r in nested},
                         {'review:1': 'continue', 'review:2': 'accept'})
        self.assertEqual(json.loads(parent.requests[-1].messages[-1].result.content), {'text': 'child accepted'})
