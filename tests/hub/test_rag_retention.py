"""Application selection uses current owner evidence and preserves failed requests."""

import unittest
from unittest.mock import patch

from hub.backend.project.rag_management import RAGManagement
from llm.services.infrastructure.storage import tree_snapshot
from tests.llm import test_rag_components as fixtures


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
