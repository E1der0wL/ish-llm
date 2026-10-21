"""Application selection uses current owner evidence and preserves failed requests."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from hub.backend.project.rag_management import RAGManagement
from llm.services.infrastructure.storage import tree_snapshot
from tests.llm import test_rag_components as fixtures
from tests.llm import test_rag_job_retention as job_fixtures


class RAGRetentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.RAGTests.asyncSetUp
    add = fixtures.RAGTests.add

    def manager(self):
        return RAGManagement(self.rag, 'rag', self.root, self.project.id, project=self.project)

    async def test_application_explicitly_selects_all_eligible_generations(self):
        await self.add(identifier='original')
        await self.rag.aupdate_document('original', content='New source')
        plan = await self.rag.acompaction_plan()
        self.assertEqual(len(plan['candidates']), 1)
        root = self.project.paths.root / 'rag' / 'generations'
        before = tree_snapshot(root / plan['active_generation'])
        selected = await self.manager().compact_retired()
        self.assertEqual(selected, [row['generation'] for row in plan['candidates']])
        self.assertEqual([path.name for path in root.iterdir()], [plan['active_generation']])
        self.assertEqual(tree_snapshot(root / plan['active_generation']), before)
        self.assertEqual((await self.rag.aget_document('original'))['content'], 'New source')

    async def test_application_does_not_retry_or_expand_selection_after_stale_plan(self):
        await self.add(identifier='original')
        await self.rag.aupdate_document('original', content='New source')
        plan = await self.rag.acompaction_plan()
        await self.rag.aupdate_document('original', title='Published after inspection')
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        with patch.object(self.rag, 'acompaction_plan', return_value=plan):
            with self.assertRaisesRegex(ValueError, 'conflict'):
                await self.manager().compact_retired()
        self.assertEqual(tree_snapshot(root), before)


class RAGJobRetentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = job_fixtures.RAGJobRetentionTests.asyncSetUp
    manager = RAGRetentionTests.manager

    class Later:
        now = staticmethod(lambda zone: datetime.now(timezone.utc) + timedelta(seconds=120))
        fromisoformat = staticmethod(datetime.fromisoformat)

    async def test_application_selects_expired_eligible_jobs_and_preserves_pending_source(self):
        pending = await self.rag.aenqueue_document(identifier='pending', title='Pending', content='Pending source')
        root = self.project.paths.root / 'rag'
        before = tree_snapshot(root)
        manager = self.manager()
        self.assertEqual((await manager.plan_job_retention(None))['selected'], [])
        self.assertEqual((await manager.plan_job_retention(60))['selected'], [])
        with patch('hub.backend.project.rag_management.datetime', self.Later):
            review = await manager.plan_job_retention(60)
            self.assertEqual(review['selected'], [self.job['id']])
            self.assertEqual(tree_snapshot(root), before)
            self.assertEqual(await manager.apply_job_retention(60, review), [self.job['id']])
        self.assertEqual([job['id'] for job in await self.rag.ajobs()], [pending['id']])
        self.assertEqual((await self.rag.aget_document(self.identifier))['title'], 'Manual')
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_review_edits_policy_changes_and_new_job_refuse_without_deletion(self):
        manager = self.manager()
        root = self.project.paths.root / 'rag'
        with patch('hub.backend.project.rag_management.datetime', self.Later):
            review = await manager.plan_job_retention(60)
            before = tree_snapshot(root)
            with self.assertRaisesRegex(ValueError, 'conflict'):
                await manager.apply_job_retention(90, review)
            review['selected'].clear()
            with self.assertRaisesRegex(ValueError, 'conflict'):
                await manager.apply_job_retention(60, review)
            self.assertEqual(tree_snapshot(root), before)
            review = await manager.plan_job_retention(60)
            await self.rag.aenqueue_document(identifier='pending', title='Pending', content='Pending source')
            before = tree_snapshot(root)
            with self.assertRaisesRegex(ValueError, 'conflict'):
                await manager.apply_job_retention(60, review)
            self.assertEqual(tree_snapshot(root), before)
        self.assertEqual(self.effects.read_text(), 'called\n')

    async def test_retired_backend_settings_and_invalid_application_age_are_rejected(self):
        before = tree_snapshot(self.project.paths.root / 'rag')
        for section in ('policy', 'config'):
            with self.subTest(section=section), self.assertRaises(ValueError):
                await self.rag.aconfigure({section: {'retention': {'job_max_age_seconds': 60}}})
        for maximum in (True, 0, -1, '60', float('nan'), float('inf')):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                await self.manager().plan_job_retention(maximum)
        self.assertEqual(tree_snapshot(self.project.paths.root / 'rag'), before)
        self.assertEqual(self.effects.read_text(), 'called\n')
