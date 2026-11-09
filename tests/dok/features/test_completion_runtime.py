"""Registration failures preserve saved data and cancellation cannot publish late work."""
import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from dok.features.completion.analysis import HelpAnalysis
from dok.features.completion.config import CompletionConfig
from dok.features.completion.models import Candidate, MergeMode, Registration
from dok.features.completion.runtime import CompletionRuntime, Phase, Request
from dok.integrations.ish.completion import LookupOutput


class CompletionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.published, self.completed, self.progress = [], [], []
        self.agent = SimpleNamespace(analyze=AsyncMock(return_value=None))
        self.runtime = CompletionRuntime(self.directory, self.published.append, lambda: None,
                                         self.agent, self.completed.append, progressed=self.progress.append)
        store = self.runtime.store
        store.update(store.load(), lambda catalog: catalog.put(Registration(("tool",), items=(Candidate("--old"),))))
        self.runtime.ensure_loaded()
        await self.runtime._task
        self.original = store.path.read_bytes()

    async def asyncTearDown(self):
        self.runtime.close()
        if self.runtime._task:
            await asyncio.gather(self.runtime._task, return_exceptions=True)

    async def test_empty_truncated_unattributed_and_conflicting_outputs_preserve_existing_catalog(self):
        for output in (LookupOutput("tool docs", "No options"),
                       LookupOutput("tool docs", "  --new  New", truncated=True),
                       LookupOutput("different", "  --new  New"),
                       LookupOutput("tool docs", "", error="Unattributed output")):
            with self.subTest(output=output):
                self.runtime.begin(Request(("tool",), "tool docs", MergeMode.REPLACE))
                self.runtime.receive(output, CompletionConfig(hub=False))
                if self.runtime.busy:
                    await self.runtime._task
                self.assertEqual(self.runtime.phase, Phase.FAILED)
                self.assertTrue(self.completed[-1].error)
                self.assertEqual(self.runtime.store.path.read_bytes(), self.original)
        self.runtime.begin(Request(("tool",), "tool docs", MergeMode.REPLACE))
        self.runtime.store.update(self.runtime.catalog, lambda catalog: catalog.put(Registration(("other",))))
        external = self.runtime.store.path.read_bytes()
        self.runtime.receive(LookupOutput("tool docs", "  --new  New"), CompletionConfig(hub=False))
        await self.runtime._task
        self.assertEqual(self.runtime.phase, Phase.FAILED)
        self.assertEqual(self.runtime.store.path.read_bytes(), external)

    async def test_hub_failure_uses_local_evidence_and_registers_once(self):
        self.agent.analyze.side_effect = TimeoutError("Hub deadline")
        self.runtime.begin(Request(("tool",), "tool docs", MergeMode.MERGE))
        output = LookupOutput("tool docs", "  --new  New")
        self.runtime.receive(output, CompletionConfig())
        await self.runtime._task
        self.assertEqual(self.runtime.phase, Phase.IDLE)
        self.assertEqual([item.name for item in self.runtime.catalog.get(("tool",)).items], ["--old", "--new"])
        self.assertEqual(self.runtime.catalog.get(("tool",)).items[-1].help, "New")
        self.assertEqual(len(self.completed), 1)
        self.assertFalse(self.completed[0].error)
        self.assertEqual([(item.phase, item.hub) for item in self.progress], [
            (Phase.EXECUTING, False), (Phase.ANALYZING, True), (Phase.ANALYZING, False), (Phase.SAVING, False)])
        self.runtime.receive(output, CompletionConfig())
        self.agent.analyze.assert_awaited_once()
        self.assertEqual(len(self.completed), 1)

    async def test_closing_analysis_cannot_publish_a_late_result(self):
        started, released = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            started.set()
            try:
                await released.wait()
            except asyncio.CancelledError:
                await released.wait()
            return HelpAnalysis.local("  --new  New")

        self.agent.analyze.side_effect = delayed
        self.runtime.begin(Request(("tool",), "tool docs", MergeMode.REPLACE))
        self.runtime.receive(LookupOutput("tool docs", "  --new  New"), CompletionConfig())
        await asyncio.wait_for(started.wait(), 1)
        self.runtime.close()
        await asyncio.sleep(0)
        self.assertTrue(self.runtime.busy)
        released.set()
        await self.runtime._task
        self.assertEqual(self.runtime.store.path.read_bytes(), self.original)
        self.assertEqual(self.completed, [])
        self.assertEqual(len(self.published), 1)

    async def test_disable_during_lookup_clears_progress_and_ignores_late_output(self):
        self.runtime.begin(Request(("tool",), "tool docs", MergeMode.REPLACE))
        self.runtime.cancel_analysis()
        self.assertEqual(self.progress[-1].phase, Phase.IDLE)
        self.assertIsNone(self.runtime.request)
        self.runtime.receive(LookupOutput("tool docs", "  --new  New"), CompletionConfig())
        self.assertEqual(self.runtime.store.path.read_bytes(), self.original)
        self.assertEqual(self.completed, [])

    async def test_management_save_publishes_once_and_rejects_stale_drafts(self):
        expected = self.runtime.catalog
        draft = expected.edit_registration(None, Registration(('manual',), items=(Candidate('--new'),)))
        completed = []
        self.assertTrue(self.runtime.save_changes(expected, draft, completed.append))
        await self.runtime._task
        self.assertEqual(completed, [''])
        self.assertEqual(self.runtime.store.load(), self.runtime.catalog)
        self.assertEqual(self.runtime.catalog.revision, expected.revision + 1)
        self.assertEqual(len(self.published), 2)
        written = self.runtime.store.path.read_bytes()
        self.assertTrue(self.runtime.save_changes(expected, draft, completed.append))
        await self.runtime._task
        self.assertIn('changed', completed[-1])
        self.assertEqual(self.runtime.store.path.read_bytes(), written)
        self.assertEqual(len(self.published), 2)
        self.agent.analyze.assert_not_awaited()

    async def test_lookup_blocks_management_save_but_failure_keeps_catalog_editable(self):
        self.runtime.begin(Request(('tool',), 'tool docs', MergeMode.REPLACE))
        completed = []
        self.assertFalse(self.runtime.save_changes(self.runtime.catalog, self.runtime.catalog, completed.append))
        self.runtime.failed('No output')
        self.assertTrue(self.runtime.catalog_ready)
        self.assertTrue(self.runtime.save_changes(self.runtime.catalog, self.runtime.catalog, completed.append))
        await self.runtime._task
        self.assertEqual(completed, [''])

    async def test_registration_count_uses_collection_policy(self):
        expected = self.runtime.catalog
        draft = expected.put(expected.get(('tool',)).edit_item(None, Candidate('--new', help='manual', locked=True)))
        self.runtime.save_changes(expected, draft, lambda error: None)
        await self.runtime._task
        self.runtime.begin(Request(('tool',), 'tool docs', MergeMode.MERGE))
        self.runtime.receive(LookupOutput('tool docs', '  --new  New'), CompletionConfig(hub=False))
        await self.runtime._task
        self.assertEqual(self.completed[-1].count, 0)
        self.assertEqual(self.runtime.catalog.get(('tool',)).item('--new').help, 'manual')
