"""Unverified answer observations cannot change accepted history or execution."""

import asyncio
from dataclasses import replace
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from hub.backend.observability.drafts import AnswerDrafts
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import AnswerDraft, ChatMessage
from hub.ui.presentation import present, present_message
from hub.widget.transcript.conversation import ConversationControl
from llm.components.processing import CompletionSession
from llm.components.review import CompletionReview, ReviewAction
from llm.components.tools import ToolComponent
from llm.core.model_protocol import StopReason
from llm.core.results import CompletionRecords, CompletionResult, EngineDelta, EngineOutput
from llm.engines.events import EngineEvent, EngineEventType as E
from llm.engines.loop import LoopEngine
from llm.engines.loop.records import LoopStepMetadata
from llm.llm import LargeLanguageModel
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_completion_processing import Feature, Processor
from tests.llm.test_completion_review import POLICY


def run(identifier='r', status='running'):
    metadata = {}
    CompletionRecords.initialize(metadata)
    return NS(id=identifier, status=status, metadata=metadata)


def step(identifier='s', iteration=1, scope=None):
    return NS(id=identifier, kind='llm', name=LoopEngine.COMPLETION_STEP_NAME,
              metadata=LoopStepMetadata.completion(iteration, LoopEngine.checkpoint_name, scope))


def start(drafts, record, value=None):
    value = value or step()
    drafts.observe(record, EngineEvent(E.STEP_STARTED, step_id=value.id, kind=value.kind,
                                      name=value.name, metadata=value.metadata))


def delta(text, sequence, step_id='s', **kwargs):
    return EngineDelta(output_id=step_id, step_id=step_id, text=text,
                       visibility='internal', sequence=sequence, **kwargs)


def emit(drafts, record, value):
    drafts.observe(record, EngineEvent(E.TEXT_DELTA, delta=value))


class DraftObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_replace_duplicates_and_publication_keep_one_candidate(self):
        drafts, record = AnswerDrafts(), run()
        handle = NS(aview=AsyncMock())
        start(drafts, record)
        emit(drafts, record, delta('A', 1))
        emit(drafts, record, delta('B', 2))
        emit(drafts, record, delta('duplicate', 2))
        self.assertEqual((await drafts.read(handle, record, ())).text, 'AB')
        emit(drafts, record, delta('교체', 3, operation='replace'))
        self.assertEqual((await drafts.read(handle, record, ())).text, '교체')
        final = EngineOutput('s', '교체', step_id='s', visibility='internal', sequence=4)
        drafts.observe(record, EngineEvent(E.STEP_COMPLETED, step_id='s', output=final))
        self.assertEqual((await drafts.read(handle, record, ())).phase, AnswerDraft.REVIEWING)
        drafts.observe(record, EngineEvent(E.OUTPUT, output=EngineOutput('root', 'Accepted', sequence=5)))
        self.assertIsNone(await drafts.read(handle, record, ()))
        handle.aview.assert_not_awaited()

    async def test_gap_reconciles_atomic_snapshot_and_buffered_events_once(self):
        drafts, record = AnswerDrafts(), run()
        start(drafts, record)
        emit(drafts, record, delta('one', 1))
        emit(drafts, record, delta('three', 3))
        self.assertTrue(drafts.runs['r'].needs_sync)
        async def recover(**kwargs):
            emit(drafts, record, delta('four', 4))
            return NS(run=record, cursor=3, outputs=[EngineOutput(
                's', 'onetwothree', step_id='s', visibility='internal', sequence=3, final=False)])
        handle = NS(aview=AsyncMock(side_effect=recover))
        self.assertEqual((await drafts.read(handle, record, (step(),))).text, 'onetwothreefour')
        self.assertEqual((await drafts.read(handle, record, (step(),))).text, 'onetwothreefour')
        handle.aview.assert_awaited_once()
        drafts.invalidate()
        await drafts.read(handle, record, (step(),))
        self.assertEqual(handle.aview.await_count, 2)

    async def test_helpers_nested_steps_tools_and_previous_candidate_are_not_answers(self):
        drafts, record = AnswerDrafts(), run()
        handle = NS(aview=AsyncMock())
        start(drafts, record)
        emit(drafts, record, delta('candidate', 1))
        start(drafts, record, step('nested', 99, 'graph/node'))
        emit(drafts, record, delta('nested secret', 2, 'nested'))
        drafts.observe(record, EngineEvent(E.STEP_STARTED, step_id='helper', kind='memory', name='summary'))
        emit(drafts, record, delta('helper secret', 3, 'helper'))
        self.assertEqual((await drafts.read(handle, record, ())).text, 'candidate')
        drafts.observe(record, EngineEvent(E.COMPLETION, completion=CompletionResult(
            step_id='s', termination=StopReason.TOOLS)))
        self.assertIsNone(await drafts.read(handle, record, ()))
        start(drafts, record, step('s2', 2))
        self.assertIsNone(await drafts.read(handle, record, ()))
        emit(drafts, record, delta('replacement', 4, 's2'))
        self.assertEqual((await drafts.read(handle, record, ())).text, 'replacement')
        emit(drafts, record, delta('', 5, 's2', operation='replace'))
        self.assertIsNone(await drafts.read(handle, record, ()))

    async def test_sessions_are_isolated_and_terminal_runs_discard_drafts(self):
        drafts, handle = AnswerDrafts(), NS(aview=AsyncMock())
        for identifier in ('a', 'b'):
            start(drafts, run(identifier))
            emit(drafts, run(identifier), delta(identifier, 1))
        self.assertEqual((await drafts.read(handle, run('b'), ())).text, 'b')
        for status in ('completed', 'failed', 'interrupted', 'cancelled', 'paused'):
            self.assertIsNone(await drafts.read(handle, run('a', status), ()))
        self.assertEqual((await drafts.read(handle, run('b'), ())).text, 'b')

    async def test_reconnect_uses_internal_snapshot_without_promoting_it(self):
        record = run()
        output = EngineOutput('s', 'candidate', step_id='s', visibility='internal', sequence=3)
        handle = NS(aview=AsyncMock(return_value=NS(run=record, cursor=3, outputs=[output])))
        drafts = AnswerDrafts()
        self.assertEqual((await drafts.read(handle, record, (step(),))).phase, AnswerDraft.REVIEWING)
        self.assertEqual(output.visibility, 'internal')
        drafts.clear()
        handle.aview.return_value.run = run(status='failed')
        self.assertIsNone(await drafts.read(handle, record, (step(),)))

    async def test_terminal_event_during_reconciliation_cannot_restore_old_draft(self):
        drafts, record = AnswerDrafts(), run()
        async def recover(**kwargs):
            drafts.forget(record.id)
            return NS(run=record, cursor=1, outputs=[EngineOutput(
                's', 'stale', step_id='s', visibility='internal', sequence=1)])
        self.assertIsNone(await drafts.read(NS(aview=AsyncMock(side_effect=recover)), record, (step(),)))
        self.assertNotIn(record.id, drafts.runs)

    async def test_persisted_answer_wins_even_if_publication_event_is_delayed(self):
        from hub.backend.observability.snapshot import SnapshotReader
        from llm.core.models import Message
        reader = SnapshotReader()
        reader.run_display = AsyncMock(return_value=('', 1.0))
        messages = [Message(id='a', role='assistant', content='Accepted answer', status='streaming', run_id='r')]
        shown = await reader.messages(None, messages, run_records={'r': run()},
                                      drafts={'r': AnswerDraft('Old draft')})
        self.assertEqual(shown[-1].text, 'Accepted answer')
        self.assertIsNone(shown[-1].draft)

    def test_draft_rendering_does_not_publish_tag_objects_and_phase_only_reuses_body(self):
        source = ChatMessage('assistant', '', id='a', status='streaming',
                             draft=AnswerDraft('```python\nprint(1)\n```\n<hub-next>Next</hub-next>'))
        language = Language()
        shown = present_message(source, language)
        self.assertEqual(source.text, '')
        self.assertNotIn('hub-next', shown.text)
        control = ConversationControl((shown,), HubTheme())
        try:
            control.create_content(80, 20)
            count = control._message_cache.body_renders
            self.assertFalse(control.objects)
            control.messages = (present_message(replace(source, draft=replace(
                source.draft, phase=AnswerDraft.REVIEWING)), language),)
            control.create_content(80, 20)
            self.assertEqual(control._message_cache.body_renders, count)
            control.messages = (replace(shown, draft=None, status='completed', phase=''),)
            control.create_content(80, 20)
            self.assertTrue(control.objects)
        finally:
            control.renderers.close()


class DraftIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, provider, review):
        processor = Processor('draft-review', lambda _: review)
        processor.review_revision = 'draft-test-v1'
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        def factory(config):
            return LargeLanguageModel(config.workspace,
                components=[ToolComponent(), Feature('reviewer', processor)],
                engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
        runtime = HubRuntime(HubConfig(directory.name, auto_title=False,
            agent_definition={'engine': 'loop', 'completion': {'model': 'test/model'}},
            project_config={'parameters': {'engines': {'loop': {'policy': {'final_review': POLICY}}}}}), factory)
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        await runtime.new_session()
        return runtime, runtime.sessions[runtime.selected_id]

    async def observe(self, runtime, predicate):
        async with asyncio.timeout(15):
            while True:
                snapshot = await runtime.snapshot()
                if predicate(snapshot):
                    return snapshot
                await asyncio.sleep(.01)

    async def test_chunks_arrive_before_review_and_only_accepted_answer_enters_history(self):
        gates = [threading.Event(), threading.Event()]
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(lambda: [g.set() for g in gates])
        class Review(CompletionSession):
            async def review_final(self, candidate):
                entered.set()
                await release.wait()
                return CompletionReview(ReviewAction.ACCEPT, '')
        def provider(**request):
            self.assertTrue(request['stream'])
            for gate, text, finish in zip(gates, ('First ', 'second'), (None, 'stop')):
                if not gate.wait(15):
                    raise TimeoutError('chunk gate')
                yield {'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': finish}]}
        runtime, session = await self.make(provider, Review())
        try:
            await runtime.submit(session.id, 'Explain this')
            gates[0].set()
            snapshot = await self.observe(runtime, lambda s: s.messages and s.messages[-1].draft is not None)
            message = snapshot.messages[-1]
            self.assertEqual((message.text, message.draft.text), ('', 'First '))
            self.assertEqual(present(snapshot, runtime.t).messages[-1].text, 'First ')
            gates[1].set()
            await asyncio.wait_for(entered.wait(), 15)
            snapshot = await self.observe(runtime, lambda s: s.messages[-1].draft is not None
                                          and s.messages[-1].draft.phase == AnswerDraft.REVIEWING)
            self.assertEqual(snapshot.messages[-1].id, message.id)
            self.assertEqual((await session.aconversation())[-1].content, '')
            release.set()
            await session.run.wait_idle()
            final = (await runtime.snapshot()).messages[-1]
            self.assertEqual((final.id, final.text, final.draft), (message.id, 'First second', None))
            self.assertEqual((await session.aconversation())[-1].content, 'First second')
        finally:
            release.set()
            for gate in gates:
                gate.set()

    async def test_continuation_replaces_draft_in_place_and_failure_removes_it(self):
        from tests.llm.test_loop import ScriptedCompletion, chunk
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        class Review(CompletionSession):
            attempt = 0
            async def review_final(self, candidate):
                index = self.attempt
                self.attempt += 1
                entered[index].set()
                await release[index].wait()
                return CompletionReview(ReviewAction.CONTINUE if index == 0 else ReviewAction.BLOCK, 'Needs correction')
        provider = ScriptedCompletion([chunk('Draft one', finish='stop')], [chunk('Draft two', finish='stop')])
        runtime, session = await self.make(provider, Review())
        try:
            await runtime.submit(session.id, 'Explain this')
            await asyncio.wait_for(entered[0].wait(), 15)
            first = await self.observe(runtime, lambda s: s.messages[-1].draft is not None)
            release[0].set()
            await asyncio.wait_for(entered[1].wait(), 15)
            second = await self.observe(runtime, lambda s: s.messages[-1].draft is not None
                                        and s.messages[-1].draft.text == 'Draft two')
            self.assertEqual(first.messages[-1].id, second.messages[-1].id)
            release[1].set()
            await session.run.wait_idle()
            final = await runtime.snapshot()
            self.assertEqual(final.messages[-1].status, 'failed')
            self.assertIsNone(final.messages[-1].draft)
            self.assertEqual((await session.aconversation())[-1].content, '')
            shown = present(final, runtime.t)
            self.assertFalse(any('Draft one' in m.text or 'Draft two' in m.text for m in shown.messages))
        finally:
            for gate in release:
                gate.set()

    async def test_interrupt_during_review_does_not_publish_the_candidate(self):
        from tests.llm.test_loop import ScriptedCompletion, chunk
        entered = asyncio.Event()
        class Review(CompletionSession):
            async def review_final(self, candidate):
                entered.set()
                await asyncio.Event().wait()
        runtime, session = await self.make(ScriptedCompletion([chunk('Unverified', finish='stop')]), Review())
        await runtime.submit(session.id, 'Explain this')
        await asyncio.wait_for(entered.wait(), 15)
        await self.observe(runtime, lambda s: s.messages[-1].draft is not None)
        await session.run.interrupt()
        await session.run.wait_idle()
        final = (await runtime.snapshot()).messages[-1]
        self.assertIsNone(final.draft)
        self.assertEqual((await session.aconversation())[-1].content, '')
