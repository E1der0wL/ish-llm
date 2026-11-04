"""Resource dates follow each public record contract without modifying source files."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from hub.backend.project.management_values import ResourceTimestamps
from hub.backend.project.record_management import RecordManagement
from hub.backend.project.skill_registration import import_skill
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.tools.management import ToolManagement
from llm.core.skills import SkillDocument
from llm.components.skills import SkillRecord


class ResourceTimestampTests(unittest.TestCase):
    def test_dates_preserve_supplied_history_and_do_not_mutate_inputs_or_backfill_reads(self):
        value = {'metadata': {'created_at': '2020-01-01T00:00:00+00:00', 'custom': 'kept'}}
        original = deepcopy(value)
        created = ResourceTimestamps.created(value)
        self.assertEqual(created['metadata']['updated_at'], original['metadata']['created_at'])
        self.assertEqual(value, original)
        changed = ResourceTimestamps.modified(created)
        self.assertEqual(changed['metadata']['created_at'], original['metadata']['created_at'])
        self.assertEqual(changed['metadata']['custom'], 'kept')
        self.assertGreater(changed['metadata']['updated_at'], created['metadata']['updated_at'])
        self.assertEqual(ResourceTimestamps.latest({}), '')
        self.assertEqual(ResourceTimestamps.created({'metadata': None}), {'metadata': None})

    def test_skill_frontmatter_keeps_body_fields_and_native_record_shape(self):
        document = SkillDocument.compose(name='guide', description='A guide', body='Keep exact body.\n',
            license='MIT', metadata={'custom': 'kept'})
        value = SkillRecord.markdown_change(document.text)
        stamped = ResourceTimestamps.skill(value)
        self.assertEqual(set(stamped), set(value))
        parsed = SkillDocument.parse(stamped['markdown'])
        self.assertEqual(parsed.body, document.body)
        self.assertEqual(parsed.frontmatter['license'], 'MIT')
        self.assertEqual(parsed.frontmatter['metadata']['custom'], 'kept')
        self.assertEqual(parsed.frontmatter['metadata']['created_at'], parsed.frontmatter['metadata']['updated_at'])
        self.assertEqual(ResourceTimestamps.skill(stamped), stamped)
        self.assertEqual(value['markdown'], document.text)

    def test_skill_without_metadata_uses_yaml_serializable_string_keys(self):
        value = SkillRecord.markdown_change(SkillDocument.compose(
            name='guide', description='A guide', body='Body').text)
        for modified in (False, True):
            with self.subTest(modified=modified):
                stored = ResourceTimestamps.skill(value, modified=modified)
                parsed = SkillDocument.parse(stored['markdown'])
                self.assertTrue(parsed.frontmatter['metadata']['updated_at'])
                self.assertEqual(parsed.body, SkillDocument.parse(value['markdown']).body)
                self.assertNotIn('metadata', SkillDocument.parse(value['markdown']).frontmatter)


class ResourceTimestampIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = HubRuntime(HubConfig(self.root / 'hub', file_root=self.root, auto_title=False))
        self.addAsyncCleanup(self.runtime.close)
        await self.runtime.start()

    async def test_imported_skill_dates_files_edit_revision_and_original_are_preserved(self):
        source = self.root / 'imported-guide'
        (source / 'scripts').mkdir(parents=True)
        original = SkillDocument.compose(name=source.name, description='Imported', body='Original body.\n',
                                         metadata={'custom': 'kept'}).text
        (source / 'SKILL.md').write_text(original)
        (source / 'scripts' / 'check.py').write_text('print(1)\n')
        data = await self.runtime.project.components.aget('skills')
        await import_skill(data, source)
        manager = await RecordManagement.for_project(self.runtime.project, 'skills', root=self.root)
        rows = await manager.list()
        self.assertTrue(next(row['modified'] for row in rows if row['name'] == source.name))
        record = await manager.read(source.name)
        document = SkillDocument.parse(record['text'])
        created = document.frontmatter['metadata']['created_at']
        self.assertEqual(document.frontmatter['metadata']['updated_at'], created)
        self.assertEqual((source / 'SKILL.md').read_text(), original)
        self.assertIn('scripts/check.py', await data.alist_files(source.name))
        updated = SkillDocument.compose(name=source.name, description='Updated', body='Changed body.\n',
                                        metadata=document.frontmatter['metadata']).text
        await manager.execute('save', {'name': source.name, 'text': updated}, record['version'])
        current = await manager.read(source.name)
        edited = SkillDocument.parse(current['text'])
        self.assertEqual(edited.frontmatter['metadata']['created_at'], created)
        self.assertGreaterEqual(edited.frontmatter['metadata']['updated_at'], created)
        self.assertEqual(edited.body, 'Changed body.\n')
        with self.assertRaises(ValueError):
            await manager.execute('save', {'name': source.name, 'text': updated}, record['version'])
        self.assertEqual(await manager.read(source.name), current)

    async def test_generic_definitions_use_shared_dates_but_native_memory_and_tool_owners_remain(self):
        definitions = {
            'workflows': {'schema_version': 1, 'entry': 'end', 'nodes': {'end': {'type': 'end'}}, 'edges': []},
            'prompts': {'messages': [{'role': 'system', 'content': 'Test'}]},
            'mcp': {'transport': 'stdio', 'command': 'never-executed'},
        }
        for component, value in definitions.items():
            with self.subTest(component=component):
                original = deepcopy(value)
                manager = await RecordManagement.for_project(self.runtime.project, component, root=self.root)
                await manager.create('new-resource', value)
                self.assertEqual(value, original)
                record = await manager.read('new-resource')
                stored = json.loads(record['text'])
                created = stored['metadata']['created_at']
                self.assertIsNotNone(datetime.fromisoformat(created).utcoffset())
                self.assertEqual(stored['metadata']['updated_at'], created)
                stored['metadata']['custom'] = 'changed'
                await manager.save('new-resource', stored, record['version'])
                after = json.loads((await manager.read('new-resource'))['text'])
                self.assertEqual(after['metadata']['created_at'], created)
                self.assertEqual(after['metadata']['custom'], 'changed')
                self.assertTrue(next(row['modified'] for row in await manager.list() if row['name'] == 'new-resource'))
        memory = await RecordManagement.for_project(self.runtime.project, 'memory')
        await memory.create('note', {'content': 'A note', 'scope': 'project', 'status': 'confirmed'})
        native = await memory.data.aload('note')
        self.assertTrue(native['created_at'])
        self.assertEqual(native['created_at'], native['updated_at'])
        self.assertNotIn('created_at', native.get('metadata', {}))
        source = self.root / 'sample_tool'
        source.mkdir()
        (source / 'sample_tool.py').write_text('def main(value: str) -> str:\n    return value\n')
        tools = await ToolManagement.for_project(self.runtime.project, root=self.root)
        entries = await tools.import_package(str(source))
        stamp = next(row['modified'] for row in entries if row['name'] == source.name)
        self.assertIsNotNone(datetime.fromisoformat(stamp).utcoffset())
        self.assertNotIn('metadata', await tools.data.aload(source.name))


class SkillImportFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_stamp_conflict_does_not_overwrite_or_delete_successfully_imported_package(self):
        value = SkillRecord.markdown_change(SkillDocument.compose(name='guide', description='Guide', body='Body').text)
        data = AsyncMock()
        data.aimport_directory.return_value = 'guide'
        data.asnapshot.return_value = {'data': value, 'version': 'captured-revision'}
        data.awrite_markdown.side_effect = ValueError('revision changed')
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            await import_skill(data, '/explicit/source/guide')
        self.assertEqual(data.awrite_markdown.call_args.kwargs, {'expected_revision': 'captured-revision'})
        data.adelete.assert_not_called()
