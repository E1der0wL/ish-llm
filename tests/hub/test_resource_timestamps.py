"""Hub observes owner-managed times without changing definitions or adding writes."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.project.record_management import RecordManagement, SkillManagement, DocumentManagement
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.management import ToolManagement
from llm.core.record_times import RecordTimes
from llm.core.skills import SkillDocument


class ResourceTimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = HubRuntime(HubConfig(self.root / 'hub', file_root=self.root, auto_title=False))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()

    async def test_skill_import_and_noop_save_preserve_markdown_revision_and_times(self):
        source = self.root / 'imported-guide'
        (source / 'scripts').mkdir(parents=True)
        original = SkillDocument.compose(name=source.name, description='Imported', body='Original body.\n',
                                         metadata={'custom': 'kept', 'updated_at': 'user metadata'}).text
        (source / 'SKILL.md').write_text(original)
        (source / 'scripts' / 'check.py').write_text('print(1)\n')
        manager = await RecordManagement.for_project(self.runtime.project, 'skills', root=self.root)
        data = manager.data
        with patch.object(data, 'awrite_markdown', wraps=data.awrite_markdown) as write:
            rows = await manager.execute('import', str(source / 'SKILL.md'))
            write.assert_not_called()
        times = RecordTimes.read(await data.aget_times(source.name))
        # Import may add files/modes after initial creation; keep the owner's
        # final times instead of forcing creation and update to be identical.
        self.assertTrue(times.created_at)
        self.assertTrue(times.updated_at)
        self.assertEqual(next(row['modified'] for row in rows if row['name'] == source.name), times.updated_at)
        record = await manager.read(source.name)
        self.assertEqual(record['text'], original)
        self.assertEqual((source / 'SKILL.md').read_text(), original)
        self.assertIn('scripts/check.py', await data.alist_files(source.name))
        await manager.execute('save', {'name': source.name, 'text': original}, record['version'])
        self.assertEqual(await manager.read(source.name), record)
        self.assertEqual(RecordTimes.read(await data.aget_times(source.name)), times)
        updated = original.replace('Original body.', 'Changed body.')
        await manager.execute('save', {'name': source.name, 'text': updated}, record['version'])
        current = await manager.read(source.name)
        changed = RecordTimes.read(await data.aget_times(source.name))
        self.assertEqual(changed.created_at, times.created_at)
        self.assertNotEqual(changed.updated_at, times.updated_at)
        self.assertEqual(current['text'], updated)
        with self.assertRaises(ValueError):
            await manager.execute('save', {'name': source.name, 'text': original}, record['version'])
        self.assertEqual(await manager.read(source.name), current)
        self.assertEqual(RecordTimes.read(await data.aget_times(source.name)), changed)
        # A later native edit must be visible without consulting frontmatter dates.
        await data.awrite_markdown(source.name, updated.replace('Changed', 'Native'),
                                   expected_revision=current['version'])
        rows = await manager.list()
        self.assertEqual(next(row['modified'] for row in rows if row['name'] == source.name),
                         RecordTimes.read(await data.aget_times(source.name)).updated_at)

    async def test_definition_managers_keep_payloads_and_observe_native_edits(self):
        definitions = {
            'workflows': {'schema_version': 1, 'entry': 'end', 'nodes': {'end': {'type': 'end'}}, 'edges': []},
            'prompts': {'messages': [{'role': 'system', 'content': 'Test'}]},
            'mcp': {'transport': 'stdio', 'command': 'never-executed'},
            'agents': {'engine': 'loop', 'tools': []},
        }
        for component, value in definitions.items():
            with self.subTest(component=component):
                value['metadata'] = {'created_at': 'arbitrary user value', 'updated_at': 'not a managed date'}
                original = deepcopy(value)
                manager = await RecordManagement.for_project(self.runtime.project, component, root=self.root)
                await manager.create('new-resource', value)
                record = await manager.read('new-resource')
                before = RecordTimes.read(await manager.data.aget_times('new-resource'))
                self.assertEqual(json.loads(record['text']), original)
                self.assertEqual(value, original)
                self.assertEqual(before.created_at, before.updated_at)
                await manager.save('new-resource', value, record['version'])
                self.assertEqual(await manager.read('new-resource'), record)
                self.assertEqual(RecordTimes.read(await manager.data.aget_times('new-resource')), before)
                changed = deepcopy(value)
                changed['metadata']['custom'] = 'changed outside Hub'
                if component == 'agents':
                    await manager.data.arevise('new-resource', changed, expected_revision=record['version'])
                else:
                    await manager.data.asave('new-resource', changed, expected_version=record['version'])
                after = RecordTimes.read(await manager.data.aget_times('new-resource'))
                self.assertEqual(after.created_at, before.created_at)
                self.assertNotEqual(after.updated_at, before.updated_at)
                rows = await manager.list()
                self.assertEqual(next(row['modified'] for row in rows if row['name'] == 'new-resource'),
                                 after.updated_at)
                with self.assertRaises(ValueError):
                    await manager.save('new-resource', value, record['version'])
                self.assertEqual(json.loads((await manager.read('new-resource'))['text']), changed)
                self.assertEqual(RecordTimes.read(await manager.data.aget_times('new-resource')), after)

    async def test_memory_and_tool_lists_use_native_managed_times(self):
        memory = await RecordManagement.for_project(self.runtime.project, 'memory')
        await memory.create('note', {'content': 'A note', 'scope': 'project', 'status': 'confirmed'})
        native = await memory.data.aload('note')
        self.assertEqual(next(row['modified'] for row in await memory.list() if row['name'] == 'note'),
                         native['updated_at'])
        self.assertNotIn('created_at', native.get('metadata', {}))
        source = self.root / 'sample_tool'
        source.mkdir()
        (source / 'sample_tool.py').write_text('def main(value: str) -> str:\n    return value\n')
        tools = await ToolManagement.for_project(self.runtime.project, root=self.root)
        with patch.object(type(tools.paths), 'modified_at', side_effect=AssertionError('Use public data times')):
            entries = await tools.import_package(str(source))
        times = RecordTimes.read(await tools.data.aget_times(source.name))
        self.assertEqual(next(row['modified'] for row in entries if row['name'] == source.name), times.updated_at)
        self.assertNotIn('metadata', await tools.data.aload(source.name))


class ResourceTimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_has_no_timestamp_write_or_cleanup_after_failure(self):
        data = AsyncMock()
        manager = SkillManagement(data, 'skills', Path.cwd())
        with patch.object(manager, 'list', new_callable=AsyncMock, return_value=[]):
            await manager.execute('import', 'guide')
            data.aimport_directory.assert_awaited_once_with(Path.cwd() / 'guide')
            data.asnapshot.assert_not_called()
            data.awrite_markdown.assert_not_called()
            data.aimport_directory.side_effect = OSError('package unavailable')
            with self.assertRaisesRegex(OSError, 'package unavailable'):
                await manager.execute('import', 'guide')
            data.adelete.assert_not_called()

    async def test_catalog_times_are_used_without_reading_document_bodies(self):
        data = AsyncMock()
        document = {'id': 'note', 'title': 'Note', 'description': 'Small catalog row',
                    'revision': 2, 'generation': 'generation',
                    'created_at': '2026-01-01T00:00:00+00:00', 'updated_at': '2026-01-02T00:00:00+00:00'}
        data.alist_catalog.return_value = {'documents': [document], 'next_cursor': None, 'generation': 'generation'}
        manager = DocumentManagement(data, 'rag', Path.cwd())
        rows = await manager.list()
        self.assertEqual(rows[0]['modified'], document['updated_at'])
        data.aget_document.assert_not_called()
        data.aget_times.assert_not_called()
        # Unsupported data stays an error; no metadata/Job/mtime/clock fallback.
        del document['updated_at']
        with self.assertRaises(ValueError):
            await manager.list()

    async def test_missing_owner_times_are_not_replaced_by_user_metadata(self):
        data = AsyncMock()
        data.alist.return_value = ['item']
        data.asnapshot.return_value = {'data': {'metadata': {'updated_at': '2026-01-01T00:00:00+00:00'}},
                                       'version': 'revision'}
        data.aget_times.return_value = {}
        manager = RecordManagement(data, 'prompts', Path.cwd())
        with self.assertRaises(ValueError):
            await manager.list()
        data.asave.assert_not_called()
