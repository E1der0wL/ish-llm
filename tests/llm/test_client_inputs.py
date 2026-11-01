"""An acknowledged caller key cannot create a second logical request or decision."""

import asyncio
import unittest
from pathlib import Path
import tempfile
from dataclasses import replace
from unittest.mock import patch

from tests.llm import test_loop_steering as steering
from tests.llm.test_loop import chunk, call
from tests.llm import test_long_running as long_running
from llm.llm import LargeLanguageModel, LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.support.runtime_tools import TEST_CONTRACT


class ClientInputTests(unittest.IsolatedAsyncioTestCase):
    setup_backend = steering.LoopSteeringTests.setup_backend

    async def test_memory_receipts_share_store_lifetime_and_lookup_failure_is_not_absence(self):
        from llm.services.runtime.client_inputs import InputReceipt

        model = steering.Model([chunk('once', finish='stop')], gate=False)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            engines = {'worker': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}
            async with LargeLanguageModel(workspace, components=[], engines=engines,
                                          conversation_storage='memory') as backend:
                project = await backend.projects.acreate('voice', components=[])
                await project.agents.acreate({'engine': 'worker', 'completion': {'model': 'test/model'}},
                                             identifier='worker')
                session = await project.sessions.acreate(agent_ids=['worker'])
                project_id, session_id = project.id, session.id
                with patch.object(InputReceipt, 'record', side_effect=OSError('receipt failed')):
                    with self.assertRaisesRegex(OSError, 'receipt failed'):
                        await session.run.submit('discarded', recipients=['worker'], request_key='rollback')
                self.assertIsNone(await session.run.get_receipt('rollback'))
                self.assertEqual((await session.run.astatus()).queued_count, 0)
                request = await session.run.submit('spoken', recipients=['worker'], request_key='memory-key')
                await request.wait(timeout=10)
                repeated = await session.run.submit('spoken', recipients=['worker'], request_key='memory-key')
                self.assertEqual(repeated.id, request.id)
                self.assertEqual(len(model.requests), 1)
                with patch.object(InputReceipt, 'find', side_effect=OSError('lookup failed')):
                    with self.assertRaisesRegex(OSError, 'lookup failed'):
                        await session.run.get_receipt('memory-key')
            async with LargeLanguageModel(workspace, components=[], engines=engines,
                                          conversation_storage='memory') as backend:
                restored = await (await backend.projects.aload(project_id)).sessions.aload(session_id)
                self.assertIsNone(await restored.run.get_receipt('memory-key'))
                self.assertEqual(len(model.requests), 1)

    async def test_duplicate_request_binding_and_reopen_reuse_acceptance_without_execution(self):
        model = steering.Model([chunk('first', finish='stop')], [chunk('voice', finish='stop')])
        backend, session, original, run = await self.setup_backend(model)
        request = await session.run.submit('spoken request', recipients=['worker'], request_key='utterance-1')
        duplicates = await asyncio.gather(*(session.run.submit(
            'spoken request', recipients=('worker',), request_key='utterance-1') for _ in range(3)))
        self.assertTrue(all(duplicate.id == request.id for duplicate in duplicates))
        for text in ('different', ''):
            with self.assertRaisesRegex(ValueError, 'different content or targets'):
                await session.run.submit(text, recipients=['worker'], request_key='utterance-1')
        with self.assertRaisesRegex(ValueError, 'different content or targets'):
            await session.run.steer(run.id, 'spoken request', request_key='utterance-1')
        receipt = await session.run.get_receipt('utterance-1')
        self.assertEqual(receipt.message_id, request.id)
        self.assertEqual(receipt.kind, 'request')
        self.assertIsNone(await session.run.get_receipt('not-accepted'))
        model.release.set()
        await original.wait(timeout=10)
        await request.wait(timeout=10)
        self.assertEqual(len(model.requests), 2)
        workspace = session.project.paths.root.parent.parent
        project_id, session_id = session.project.id, session.id
        await backend.shutdown()
        reopened = LargeLanguageModel(workspace, components=[], engines={
            'worker': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
        self.addAsyncCleanup(reopened.shutdown)
        restored = await (await reopened.projects.aload(project_id)).sessions.aload(session_id)
        saved = await restored.run.submit('spoken request', recipients=['worker'], request_key='utterance-1')
        self.assertEqual(saved.id, request.id)
        self.assertEqual(len(model.requests), 2)

    async def test_duplicate_instruction_is_one_message_and_current_scope_is_bound(self):
        model = steering.Model([chunk('first', finish='stop')], [chunk('revised', finish='stop')])
        _, session, request, run = await self.setup_backend(model)
        first = await session.run.steer(run.id, 'correction', request_key='utterance-2')
        duplicate = await session.run.steer(run.id, 'correction', request_key='utterance-2')
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(len(await run.ainstructions()), 1)
        model.release.set()
        await request.wait(timeout=10)
        late = await session.run.steer(run.id, 'correction', request_key='utterance-2')
        self.assertEqual(late.id, first.id)
        self.assertEqual(len(await run.ainstructions()), 1)
        self.assertEqual(len(model.requests), 2)

    async def test_receipt_storage_failure_does_not_leave_an_unreceipted_request(self):
        model = steering.Model([chunk('first', finish='stop')])
        _, session, original, _ = await self.setup_backend(model)
        from llm.services.runtime.client_inputs import InputReceipt
        with patch.object(InputReceipt, 'record', side_effect=OSError('receipt write failed')):
            with self.assertRaisesRegex(OSError, 'receipt write failed'):
                await session.run.submit('not accepted', recipients=['worker'], request_key='failed-receipt')
        self.assertIsNone(await session.run.get_receipt('failed-receipt'))
        self.assertEqual((await session.run.astatus()).queued_count, 0)
        model.release.set()
        await original.wait(timeout=10)
        self.assertEqual(len(model.requests), 1)

    async def test_approval_receipt_is_atomic_deduplicated_and_never_resumes_implicitly(self):
        effects = []
        async def act(arguments):
            effects.append('once')
        _, session, _ = await long_running.LongRunningTests.setup_app(self, act, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [chunk('done', finish='stop')]], tool_contract=replace(TEST_CONTRACT, approval_required=True))
        paused = (await (await session.run.submit('run', recipients=['loop'])).wait(timeout=10))[0]
        self.assertEqual(paused.data.status, 'paused')
        question, = await paused.ainteractions(pending_only=True)
        from llm.services.runtime.client_inputs import InputReceipt
        with patch.object(InputReceipt, 'record', side_effect=OSError('receipt failed')):
            with self.assertRaisesRegex(OSError, 'receipt failed'):
                await paused.arespond(question.respond('approve'), request_key='answer-1')
        self.assertIsNone(await session.run.get_receipt('answer-1'))
        self.assertEqual(await paused.ainteraction_responses(), [])
        accepted = await paused.arespond(question.respond('approve'), request_key='answer-1')
        repeated = await paused.arespond(question.respond('approve'), request_key='answer-1')
        self.assertEqual(repeated, accepted)
        self.assertEqual(effects, [])
        self.assertEqual(len(await paused.ainteraction_responses()), 1)
        with self.assertRaisesRegex(ValueError, 'different content or targets'):
            await paused.arespond(question.respond('deny'), request_key='answer-1')
        resumed = (await (await session.run.resume(paused.id, agent_id='loop')).wait(timeout=10))[0]
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, ['once'])
        self.assertEqual(await paused.arespond(question.respond('approve'), request_key='answer-1'), accepted)
        self.assertEqual(effects, ['once'])
