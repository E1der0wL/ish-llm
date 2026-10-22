"""Independent standard fixtures and native directory failure/effect assertions."""

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.skills import SkillDocument, SkillName
from llm.components.skills import SkillComponent
from llm.components.skills.storage import SkillStorage
from llm.llm import LargeLanguageModel


def markdown(name="guide", body="Read the reference.", description="Guidance for reviewing code."):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


class SkillDocumentTests(unittest.TestCase):
    def test_standard_optional_fields_and_original_text(self):
        raw = ('---\r\n# retain this comment\r\nname: guide\r\ndescription: |\r\n'
               '  Review code and verify results.\r\nlicense: MIT\r\ncompatibility: Python 3\r\n'
               'metadata:\r\n  author: me\r\n  version: "1.0"\r\n'
               'allowed-tools: Bash(git:*) Read\r\n---\r\n# Guidance\r\nDo work.\r\n')
        document = SkillDocument.parse(raw, identifier="guide")
        self.assertEqual(document.text, raw)
        self.assertEqual(document.body, '# Guidance\r\nDo work.\r\n')
        self.assertEqual(document.frontmatter['metadata'], {'author': 'me', 'version': '1.0'})
        self.assertEqual(document.frontmatter['allowed-tools'], 'Bash(git:*) Read')

    def test_empty_body_and_unicode_names_are_standard(self):
        self.assertEqual(SkillDocument.parse(markdown("검토", "")).body, "")
        self.assertEqual(SkillDocument.parse(markdown("café")).name, "café")
        for name in ("UPPER", "bad_name", "-bad", "bad-", "bad--name", "../bad", "a" * 65, ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                SkillName.validate(name)

    def test_invalid_frontmatter_is_rejected_without_coercion(self):
        invalid = ["body only", "---\nname: guide\n", '---\nname: guide\n---\n',
                   markdown().replace('name: guide', 'name: true'),
                   markdown().replace('description: Guidance for reviewing code.', 'description: null'),
                   markdown().replace('description: Guidance for reviewing code.', 'description: " "'),
                   markdown().replace('name: guide', 'name: guide\nname: other'),
                   markdown().replace('name: guide', 'name: guide\ntitle: old field'),
                   markdown().replace('name: guide', 'name: guide\nmetadata: {version: 1}'),
                   markdown().replace('name: guide', 'name: guide\nmetadata: {a: x, a: y}'),
                   markdown().replace('name: guide', 'name: guide\nmetadata: {nested: {a: b}}'),
                   markdown().replace('name: guide', 'name: guide\nmetadata: !!python/object:dict {}'),
                   markdown().replace('name: guide', 'name: guide\nallowed-tools: [Read]'),
                   markdown().replace('name: guide', 'name: guide\ncompatibility: ""')]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, UnicodeError)):
                SkillDocument.parse(value)
        with self.assertRaises(ValueError):
            SkillDocument.parse(markdown(), identifier="different")
        with self.assertRaises(ValueError):
            SkillDocument.parse(markdown(description="x" * 1025))
        # Recommendations are not hidden hard limits.
        self.assertEqual(len(SkillDocument.parse(markdown(body="x\n" * 501)).body), 1002)


class NativeSkillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.component = SkillComponent()
        self.app = LargeLanguageModel(self.root / "workspace", components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=["skills"])
        self.skills = self.project.components.skills
        await self.skills.acreate({"markdown": markdown()}, identifier="guide")
        self.package = self.project.paths.root / "skills" / "guide"
        self.state = self.project.paths.root / "skills" / "_state"

    async def put(self, path="references/readme.md", content="Reference"):
        snapshot = await self.skills.asnapshot("guide")
        return await self.skills.awrite_file("guide", path, content, expected_revision=snapshot['version'])

    def bytes(self):
        return {p.relative_to(self.project.paths.root).as_posix(): p.read_bytes()
                for p in self.project.paths.root.rglob('*') if p.is_file()}

    async def test_only_markdown_is_definition_and_operational_record_has_no_mirror(self):
        self.assertEqual((self.package / "SKILL.md").read_text(), markdown())
        self.assertFalse(list(self.project.paths.root.rglob('skill.json')))
        record = json.loads((self.state / 'records/guide.json').read_text())
        self.assertNotIn('markdown', record)
        self.assertNotIn('instructions', record)
        self.assertNotIn('frontmatter', record)
        self.assertEqual(record['document']['sha256'], hashlib.sha256(markdown().encode()).hexdigest())
        self.assertEqual(await self.skills.aload('guide'), {'markdown': markdown()})
        with self.assertRaises(ValueError):
            await self.skills.acreate({'instructions': 'Legacy'}, identifier='legacy')

    async def test_source_import_export_preserves_bytes_modes_and_empty_directories(self):
        source = self.root / 'native'
        source.mkdir()
        raw = markdown('native').replace('\n', '\r\n').encode()
        (source / 'SKILL.md').write_bytes(raw)
        (source / 'scripts').mkdir()
        script = source / 'scripts/run.py'
        script.write_bytes(b'raise AssertionError("never execute during import")\n')
        script.chmod(0o750)
        (source / 'empty').mkdir()
        self.assertEqual(await self.skills.aimport_directory(source), 'native')
        stored = self.project.paths.root / 'skills/native'
        self.assertEqual((stored / 'SKILL.md').read_bytes(), raw)
        self.assertEqual((stored / 'scripts/run.py').stat().st_mode & 0o777, 0o750)
        self.assertTrue((stored / 'empty').is_dir())
        output = self.root / 'export'
        output.mkdir()
        snapshot = await self.skills.asnapshot('native')
        await self.skills.aexport_directory('native', output / 'native', expected_revision=snapshot['version'])
        self.assertEqual((output / 'native/SKILL.md').read_bytes(), raw)
        self.assertEqual((output / 'native/scripts/run.py').read_bytes(), script.read_bytes())
        self.assertEqual((output / 'native/scripts/run.py').stat().st_mode & 0o777, 0o750)
        self.assertTrue((output / 'native/empty').is_dir())
        self.assertFalse((output / 'native/_state').exists())
        with self.assertRaises(FileExistsError):
            await self.skills.aexport_directory('native', output / 'native', expected_revision=snapshot['version'])

    async def test_missing_or_tampered_required_evidence_is_not_recreated(self):
        await self.put()
        candidates = [self.package / 'SKILL.md', self.package / 'references/readme.md',
                      self.state / 'records/guide.json', self.state / 'manifest.json']
        for path in candidates:
            original = path.read_bytes()
            path.unlink()
            with self.subTest(path=path), self.assertRaises((ValueError, FileNotFoundError)):
                await self.skills.aload('guide')
            self.assertFalse(path.exists())
            path.write_bytes(original)
            original_mode = path.stat().st_mode & 0o777
            # Restore original source permissions after the deliberate unlink/recreate.
            if path == self.package / 'SKILL.md' or path == self.package / 'references/readme.md':
                path.chmod(0o600)
            await self.skills.aload('guide')
        path = self.package / 'references/readme.md'
        path.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            await self.skills.aload('guide')
        self.assertEqual(path.read_text(), 'tampered')

    async def test_missing_operational_fields_are_not_defaulted(self):
        path = self.state / 'records/guide.json'
        original = path.read_bytes()
        catalog_path = self.state / 'manifest.json'
        original_catalog = catalog_path.read_bytes()
        for field in ('resources', 'lineage', 'files', 'directories', 'document', 'source'):
            value = json.loads(original)
            del value[field]
            path.write_text(json.dumps(value))
            catalog = json.loads(original_catalog)
            catalog['records']['guide'] = hashlib.sha256(path.read_bytes()).hexdigest()
            catalog_path.write_text(json.dumps(catalog))
            damaged = path.read_bytes()
            with self.subTest(field=field), self.assertRaises(ValueError):
                await self.skills.aload('guide')
            self.assertEqual(path.read_bytes(), damaged)
        path.write_bytes(original)
        catalog_path.write_bytes(original_catalog)

    async def test_stale_cas_and_document_file_alias_are_rejected(self):
        before = await self.skills.asnapshot('guide')
        after = await self.put()
        self.assertNotEqual(before['version'], after['version'])
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await self.skills.awrite_markdown('guide', markdown(body='stale'), expected_revision=before['version'])
        for method in (self.skills.awrite_file, self.skills.adelete_file):
            args = ('guide', 'SKILL.md', 'bad') if method == self.skills.awrite_file else ('guide', 'SKILL.md')
            with self.assertRaises(ValueError):
                await method(*args, expected_revision=after['version'])
        self.assertEqual(await self.skills.aread_markdown('guide'), markdown())

    async def test_injected_write_failure_preserves_all_source_bytes_and_revision(self):
        original = await self.put()
        before = self.bytes()
        with patch('llm.components.skills.storage.atomic_json', side_effect=OSError('operational commit failure')):
            with self.assertRaises(OSError):
                await self.skills.awrite_file('guide', 'references/readme.md', 'replacement', expected_revision=original['version'])
        self.assertEqual(await self.skills.asnapshot('guide'), original)
        # Logs are after-commit diagnostics; compare all package and operational bytes.
        for path, raw in before.items():
            if path.startswith('skills/'):
                self.assertEqual((self.project.paths.root / path).read_bytes(), raw)
        self.assertEqual(await self.skills.aread_file('guide', 'references/readme.md'), b'Reference')

    async def test_failed_import_rolls_back_new_package_and_preserves_input(self):
        source = self.root / 'imported'
        source.mkdir()
        (source / 'SKILL.md').write_text(markdown('imported'))
        (source / 'asset').write_bytes(b'original')
        original = (source / 'SKILL.md').read_bytes()
        commit = SkillStorage._commit
        calls = 0
        def fail_final(storage, *args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('publish failed')
            return commit(storage, *args)
        with patch.object(SkillStorage, '_commit', fail_final), self.assertRaises(OSError):
            await self.skills.aimport_directory(source)
        self.assertEqual(calls, 2)
        self.assertEqual(await self.skills.alist(), {'guide': {'markdown': markdown()}})
        self.assertFalse((self.project.paths.root / 'skills/imported').exists())
        self.assertEqual((source / 'SKILL.md').read_bytes(), original)
        self.assertEqual((source / 'asset').read_bytes(), b'original')

    async def test_document_changed_before_commit_cannot_publish_different_definition(self):
        from llm.services.infrastructure.storage import atomic_bytes
        original = await self.skills.asnapshot('guide')
        before = self.bytes()
        commit = SkillStorage._commit
        def replace_document(storage, identifier, value, catalog):
            atomic_bytes(storage.package(identifier) / 'SKILL.md', markdown(body='unexpected').encode())
            return commit(storage, identifier, value, catalog)
        with patch.object(SkillStorage, '_commit', replace_document):
            with self.assertRaisesRegex(ValueError, 'document changed'):
                await self.skills.awrite_markdown('guide', markdown(body='requested'),
                                                 expected_revision=original['version'])
        self.assertEqual(await self.skills.asnapshot('guide'), original)
        for path, raw in before.items():
            if path.startswith('skills/'):
                self.assertEqual((self.project.paths.root / path).read_bytes(), raw)

    async def test_native_import_rejects_links_special_files_and_name_mismatch(self):
        source = self.root / 'linked'
        source.mkdir()
        (source / 'SKILL.md').write_text(markdown('linked'))
        (source / 'outside').symlink_to(self.package / 'SKILL.md')
        with self.assertRaisesRegex(ValueError, 'linked'):
            await self.skills.aimport_directory(source)
        (source / 'outside').unlink()
        os.mkfifo(source / 'fifo')
        with self.assertRaisesRegex(ValueError, 'regular'):
            await self.skills.aimport_directory(source)
        (source / 'fifo').unlink()
        (source / 'SKILL.md').write_text(markdown('wrong'))
        with self.assertRaisesRegex(ValueError, 'match'):
            await self.skills.aimport_directory(source)
        self.assertFalse((self.project.paths.root / 'skills/linked').exists())

    async def test_reference_delete_protection_and_native_file_read_tool(self):
        snapshot = await self.put()
        await self.skills.aupdate('guide', {'resources': [{'path': 'references/readme.md'}]}, expected_version=snapshot['version'])
        snapshot = await self.skills.asnapshot('guide')
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.skills.adelete_file('guide', 'references/readme.md', expected_revision=snapshot['version'])
        tools = self.component.resolve_runtime(self.project.data, 'tools', data_factory=lambda _: self.skills)
        tool, args = tools.prepare('skill_read_resource', {'identifier': 'guide', 'path': 'references/readme.md'})
        self.assertEqual((await tool.handler(args))['text'], 'Reference')
        with self.assertRaises(ValueError):
            tools.prepare('skill_read_resource', {'identifier': 'guide', 'index': 0, 'path': 'references/readme.md'})
        await self.put(content='changed')
        with self.assertRaisesRegex(ValueError, 'edit_conflict'):
            await tool.handler(args)

    async def test_fork_clone_and_backup_keep_relations_and_files(self):
        original = await self.put()
        await self.skills.afork('guide', original['data'], identifier='child', expected_revision=original['version'])
        child = await self.skills.aload('child')
        self.assertEqual(SkillDocument.parse(child['markdown']).name, 'child')
        self.assertEqual(child['lineage'], {'parent': 'guide', 'parent_revision': original['version']})
        with self.assertRaisesRegex(ValueError, 'referenced'):
            await self.skills.adelete('guide')
        clone = await self.project.aclone()
        self.assertEqual(await clone.components.skills.aload('child'), child)
        backup = self.root / 'backup'
        await self.project.abackup(backup)
        async with LargeLanguageModel(self.root / 'restored', components=[SkillComponent()]) as other:
            restored = await other.projects.arestore_backup(backup)
            self.assertEqual(await restored.components.skills.aload('child'), child)
            self.assertEqual(await restored.components.skills.aread_file('child', 'references/readme.md'), b'Reference')

    async def test_standard_names_do_not_collide_with_operational_storage(self):
        for name in ('usage', 'records', '검토'):
            await self.skills.acreate({'markdown': markdown(name)}, identifier=name)
            self.assertEqual(SkillDocument.parse(await self.skills.aread_markdown(name)).name, name)
        with self.assertRaises(ValueError):
            await self.skills.acreate({'markdown': markdown('_state')}, identifier='_state')
        self.assertEqual(await self.skills.amodel_usage(), [])

    async def test_legacy_directory_is_rejected_and_preserved(self):
        old = self.project.paths.root / 'skills/legacy'
        old.mkdir()
        raw = b'{"instructions":"old source"}'
        (old / 'skill.json').write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'legacy'):
            await self.skills.alist()
        self.assertEqual((old / 'skill.json').read_bytes(), raw)
        self.assertFalse((old / 'SKILL.md').exists())

    async def test_lost_skill_usage_receipt_cannot_reset_project_budget(self):
        from llm.providers.observations import ModelCall, observed_call
        project = await self.app.projects.acreate(components=['skills'],
            config={'policies': {'usage': {'project_max_calls': 1}}})
        handle = self.app.project_manager.component(project.data, 'skills')
        invocation = ModelCall('custom', 'custom', lambda counter: 1,
            lambda response: {'total_tokens': 1}, lambda error: 'custom_error')
        effect = self.root / 'usage-effects.txt'
        async def call(**request):
            with effect.open('a') as stream:
                stream.write('called\n')
            return {}
        with handle.model_scope():
            await observed_call(invocation, {}, call)
        receipts = await project.components.skills.amodel_usage()
        receipt = project.paths.root / 'skills/_state/usage' / (receipts[0]['id'] + '.json')
        self.assertTrue(receipt.is_file())
        receipt.unlink()
        with handle.model_scope(), self.assertRaises((ValueError, FileNotFoundError)):
            await observed_call(invocation, {}, call)
        self.assertEqual(effect.read_text(), 'called\n')
        self.assertFalse(receipt.exists())

    async def test_process_exit_between_source_write_and_operational_commit_recovers_original(self):
        import subprocess
        import sys
        original = await self.put()
        identifier = self.project.id
        workspace = self.root / 'workspace'
        await self.app.shutdown()
        script = '''
import asyncio, os, sys
from pathlib import Path
from unittest.mock import patch
from llm.llm import LargeLanguageModel
from llm.components.skills import SkillComponent
async def main():
    async with LargeLanguageModel(Path(sys.argv[1]), components=[SkillComponent()]) as app:
        project = await app.projects.aload(sys.argv[2])
        skills = project.components.skills
        snapshot = await skills.asnapshot('guide')
        with patch('llm.components.skills.storage.SkillStorage._commit', side_effect=lambda *a: os._exit(71)):
            if sys.argv[3] == 'file':
                await skills.awrite_file('guide', 'references/readme.md', 'interrupted replacement', expected_revision=snapshot['version'])
            else:
                await skills.awrite_markdown('guide', snapshot['data']['markdown'] + '\\ninterrupted', expected_revision=snapshot['version'])
asyncio.run(main())
'''
        for operation in ('file', 'document'):
            result = await asyncio.to_thread(subprocess.run,
                [sys.executable, '-c', script, str(workspace), identifier, operation],
                capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 71, result.stderr.decode())
            async with LargeLanguageModel(workspace, components=[SkillComponent()]) as reopened:
                project = await reopened.projects.aload(identifier)
                self.assertEqual(await project.components.skills.asnapshot('guide'), original)
                self.assertEqual(await project.components.skills.aread_file('guide', 'references/readme.md'), b'Reference')
                self.assertEqual(await project.components.skills.aread_markdown('guide'), markdown())
