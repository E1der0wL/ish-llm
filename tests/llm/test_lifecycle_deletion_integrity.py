"""Parent deletion must not turn corrupt child evidence into permission to discard it."""

import unittest
from unittest.mock import patch

from tests.llm import test_conversation_turns as turns
from tests.llm import test_operation_integrity as operations
from tests.llm import test_memory_summary_integrity as summaries


class ParentDeletionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    setup_graph = turns.TurnResumeTests.setup_graph
    resume = turns.TurnResumeTests.resume

    async def reject_corrupt_child(self, kind, *, missing=False):
        for scope in ('session', 'project'):
            with self.subTest(scope=scope):
                run = await self.setup_graph()
                if kind == 'step':
                    run = await self.resume(run, 'first')
                    self.assertEqual(run.data.status, 'completed', run.data.error)
                await self.session.run.shutdown()
                if kind == 'checkpoint':
                    path = run.data.paths.state / 'checkpoints/graph/checkpoint.json'
                elif kind == 'approval':
                    request, = await run.ainteractions()
                    path = run.data.paths.state / 'interactions' / (request.id + '.json')
                elif kind == 'run':
                    path = run.data.paths.root / 'run.json'
                elif kind == 'step':
                    step = (await run.steps.alist())[0]
                    path = step.paths.root / 'step.json'
                elif kind == 'conversation':
                    path = self.session.data.paths.conversation
                else:
                    raise AssertionError(kind)
                self.assertTrue(path.is_file())
                retained = [path, self.project.paths.root / 'project.json',
                            self.session.paths.root / 'session.json', run.data.paths.root / 'run.json']
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(b'{corrupt required evidence')
                before = {item: item.read_bytes() if item.exists() else None for item in retained}
                effects = list(self.effects)
                target = self.session if scope == 'session' else self.project
                try:
                    with self.assertRaises((ValueError, OSError)):
                        await target.adelete(permanent=True)
                    self.assertEqual({item: item.read_bytes() if item.exists() else None for item in retained}, before)
                    self.assertEqual(self.effects, effects)
                finally:
                    await self.app.shutdown()

    async def test_parent_cannot_discard_corrupt_checkpoint(self):
        await self.reject_corrupt_child('checkpoint')

    async def test_parent_cannot_discard_corrupt_approval(self):
        await self.reject_corrupt_child('approval')

    async def test_parent_cannot_discard_corrupt_run(self):
        await self.reject_corrupt_child('run')

    async def test_parent_cannot_discard_corrupt_step(self):
        await self.reject_corrupt_child('step')

    async def test_parent_cannot_discard_corrupt_conversation(self):
        await self.reject_corrupt_child('conversation')

    async def test_missing_checkpoint_cannot_grant_parent_deletion(self):
        await self.reject_corrupt_child('checkpoint', missing=True)

    async def test_missing_approval_cannot_grant_parent_deletion(self):
        await self.reject_corrupt_child('approval', missing=True)

    async def test_valid_file_and_memory_domain_termination_remains_explicit(self):
        for storage in ('file', 'memory'):
            for scope in ('session', 'project'):
                with self.subTest(storage=storage, scope=scope):
                    run = await self.setup_graph(storage)
                    completed = await self.resume(run, 'first')
                    self.assertEqual(completed.data.status, 'completed', completed.data.error)
                    await self.session.run.shutdown()
                    root = self.session.paths.root if scope == 'session' else self.project.paths.root
                    if storage == 'memory':
                        self.assertFalse(self.session.paths.conversation.exists())
                    await (self.session if scope == 'session' else self.project).adelete(permanent=True)
                    self.assertFalse(root.exists())
                    self.assertEqual(self.effects, ['first', 'second'])
                    await self.app.shutdown()

    async def test_injected_step_owner_can_refuse_both_parent_deletions(self):
        run = await self.setup_graph()
        await self.session.run.shutdown()
        path = run.data.paths.root / 'run.json'
        before = path.read_bytes()
        with patch.object(self.app.step_manager.repository, 'read_backup', side_effect=ValueError('owner refusal')):
            for target in (self.session, self.project):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'owner refusal'):
                    await target.adelete(permanent=True)
                self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.effects)

    async def test_disabled_goal_session_and_run_references_block_direct_session_delete(self):
        from llm.components.goals import GoalComponent
        for kind in ('session', 'run'):
            with self.subTest(kind=kind):
                paused = await self.setup_graph()
                run = await self.resume(paused, 'first')
                self.assertEqual(run.data.status, 'completed', run.data.error)
                await self.session.run.shutdown()
                self.app.project_manager.components.register(GoalComponent())
                await self.project.components.aselect(['workflows', 'goals'])
                value = {'title': 'Keep evidence', 'objective': 'Retain source', 'status': 'active',
                         'scope': {'type': 'session', 'id': self.session.id} if kind == 'session' else {'type': 'project'},
                         'run_refs': [] if kind == 'session' else [
                             {'session_id': self.session.id, 'run_id': run.id, 'relation': 'evidence'}]}
                await self.project.components.goals.acreate(value, identifier='evidence')
                await self.project.components.aremove('goals')
                path = run.data.paths.root / 'run.json'
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'referenced'):
                    await self.session.adelete(permanent=True)
                self.assertEqual(path.read_bytes(), before)
                # Ending the whole valid Project removes both owners together.
                await self.project.adelete(permanent=True)
                self.assertFalse(self.project.paths.root.exists())
                self.assertEqual(self.effects, ['first', 'second'])
                await self.app.shutdown()


class MemoryDeletionReferencesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = summaries.SummaryStorageTests.asyncSetUp
    publish = summaries.SummaryStorageTests.publish

    async def test_disabled_memory_summary_protects_session(self):
        await self.project.components.aremove('memory')
        original = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.session.adelete(permanent=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.session.paths.root.exists())

    async def test_lost_reference_evidence_cannot_be_treated_as_no_reference(self):
        original = self.storage.manifest.read_bytes()
        self.path.unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.session.adelete(permanent=True)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.storage.manifest.read_bytes(), original)
        self.assertTrue(self.session.paths.root.exists())


class ParentOperationDeletionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = operations.OperationIntegrityTests.asyncSetUp
    open = operations.OperationIntegrityTests.open
    execute = operations.OperationIntegrityTests.execute
    file_effect = operations.OperationIntegrityTests.file_effect

    async def assert_ledger_preserved(self, target):
        effect, attempts = self.file_effect()
        run = await self.execute()
        self.assertEqual(run.data.status, 'completed', run.data.error)
        receipt = await self.session.run.aoperation('invoice-1')
        path = self.session.paths.state / 'tool_operations' / (receipt['idempotency_key'] + '.json')
        await self.session.run.shutdown()
        path.write_bytes(b'{corrupt external effect receipt')
        before = path.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            await target.adelete(permanent=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((len(attempts), effect.read_text()), (1, 'effect\n'))

    async def test_session_cannot_discard_corrupt_effect_receipt(self):
        await self.assert_ledger_preserved(self.session)

    async def test_project_cannot_discard_corrupt_effect_receipt(self):
        await self.assert_ledger_preserved(self.project)
