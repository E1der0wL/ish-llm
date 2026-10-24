"""Component record loss must not become empty storage or a new identity."""
import tempfile
import asyncio
import json
import shutil
import sys
import textwrap
from pathlib import Path
import unittest
from unittest.mock import patch
from llm.components.base import Component
from llm.components.memory import MemoryComponent
from llm.llm import LargeLanguageModel, DataComponent


class Notes(Component):
    name = directory = 'notes'


class ComponentRecordLossProbe(unittest.IsolatedAsyncioTestCase):
    async def test_generic_record_loss_is_not_an_empty_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            async with LargeLanguageModel(temporary, components=[Notes()]) as app:
                project = await app.projects.acreate(components=['notes'])
                await project.components.notes.acreate({'protected':True}, identifier='record')
                path = project.paths.root/'notes/records/record.json'
                path.unlink()
                with self.assertRaises((ValueError, OSError)):
                    await project.components.notes.alist()
                self.assertFalse(path.exists())

    async def test_lost_memory_record_cannot_be_recreated_as_a_new_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            async with LargeLanguageModel(temporary, components=[MemoryComponent()]) as app:
                project = await app.projects.acreate(components=['memory'])
                memory = project.components.memory
                await memory.acreate({'content':'original'}, identifier='record')
                generation = (await memory.aload('record'))['generation']
                path = project.paths.root/'memory/records/record.json'
                path.unlink()
                rejected = False
                try:
                    await memory.acreate({'content':'replacement'}, identifier='record')
                except (ValueError, OSError):
                    rejected = True
                replacement = path.exists() and (await memory.aload('record'))['generation'] != generation
                self.assertEqual((rejected,path.exists(),replacement),(True,False,False))


class ComponentRecordIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, component=None, *, selected=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.component = component or Notes()
        self.app = LargeLanguageModel(self.root / 'workspace', components=[self.component])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[self.component.name] if selected else [])
        if selected:
            self.data = self.project.components[self.component.name]
        self.storage = self.project.paths.root / self.component.directory

    def snapshot(self):
        return {p.relative_to(self.storage).as_posix():p.read_bytes()
                for p in self.storage.rglob('*') if p.is_file()}

    async def test_empty_and_explicit_deletion_have_an_authoritative_inventory(self):
        await self.create()
        self.assertEqual(await self.data.alist(), {})
        await self.data.acreate({'value':None}, identifier='record')
        await self.data.adelete('record')
        self.assertEqual(await self.data.alist(), {})
        manifest = json.loads((self.storage / self.component.RECORDS_MANIFEST).read_bytes())
        self.assertEqual(manifest['records'], {})
        await self.data.acreate({'value':0}, identifier='record')
        self.assertEqual(await self.data.aload('record'), {'value':0})
        await self.project.components.aremove('notes', permanent=True)
        self.assertFalse(self.storage.exists())
        self.assertEqual((await self.app.projects.aload(self.project.id)).data.initialized_components, ())

    async def test_never_created_inventory_is_empty_without_initialization(self):
        await self.create(selected=False)
        self.assertEqual(self.component.list(self.project.data), {})
        self.assertFalse(self.storage.exists())

    async def test_manifest_and_record_directory_loss_block_reselect_backup_and_removal(self):
        for lost in ('manifest', 'directory'):
            with self.subTest(lost=lost):
                await self.create()
                await self.data.acreate({'protected':True}, identifier='record')
                await self.project.components.aremove('notes')
                if lost == 'manifest':
                    (self.storage / self.component.RECORDS_MANIFEST).unlink()
                else:
                    shutil.rmtree(self.storage / 'records')
                before = self.snapshot()
                for operation in (lambda:self.project.components.aselect(['notes']),
                                  lambda:self.project.abackup(self.root / 'backup'),
                                  lambda:self.project.components.aremove('notes', permanent=True)):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                    self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / 'backup').exists())

    async def test_manifest_fields_types_ownership_and_inventory_are_required(self):
        await self.create()
        await self.data.acreate({'protected':True}, identifier='record')
        path = self.storage / self.component.RECORDS_MANIFEST
        original = json.loads(path.read_bytes())
        invalid = [{key:value for key,value in original.items() if key != field} for field in original]
        invalid += [{**original, 'schema_version':value} for value in (None, True, 1.0, '1', 2)]
        invalid += [{**original, key:value} for key,value in (
            ('component','other'), ('project_id','other'), ('records',{}), ('records',None),
            ('records',{'record':'0'*64}), ('records',{'../record':'0'*64}), ('records',{'record':None}))]
        for value in invalid:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises((ValueError, OSError)):
                    await self.data.alist()
                self.assertEqual(self.snapshot(), before)

    async def test_changed_or_undeclared_records_are_preserved_and_not_overwritten(self):
        for orphan in (False, True):
            with self.subTest(orphan=orphan):
                await self.create()
                await self.data.acreate({'protected':True}, identifier='record')
                path = self.storage / 'records' / ('orphan.json' if orphan else 'record.json')
                path.write_text('{"tampered":true}')
                before = self.snapshot()
                for operation in (lambda:self.data.alist(),
                                  lambda:self.data.asave('record', {'replacement':True}),
                                  lambda:self.data.adelete('record'), lambda:self.project.aclone()):
                    with self.assertRaises(ValueError):
                        await operation()
                    self.assertEqual(self.snapshot(), before)
                self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_memory_cache_does_not_hide_lost_or_changed_records(self):
        await self.create(MemoryComponent())
        await self.project.components.memory.aconfigure({'config':{'cache_records':2}})
        await self.data.acreate({'content':'protected'}, identifier='record')
        self.assertEqual(len(await self.data.alist()), 1)
        self.assertTrue(self.component._record_cache)
        path = self.storage / 'records/record.json'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'protected', b'corrupted'))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            await self.data.alist()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            await self.data.alist()
        self.assertFalse(path.exists())

    async def test_whole_file_package_loss_blocks_recreation_and_backup(self):
        await self.create(DataComponent())
        await self.data.acreate({'title':'protected'}, identifier='record')
        version = (await self.data.asnapshot('record'))['version']
        await self.data.awrite_file('record', 'evidence.txt', b'evidence', expected_revision=version)
        shutil.rmtree(self.storage / 'record')
        before = self.snapshot()
        for operation in (lambda:self.data.alist(),
                          lambda:self.data.acreate({'title':'replacement'}, identifier='record'),
                          lambda:self.project.abackup(self.root / 'backup'), lambda:self.project.aclone()):
            with self.assertRaisesRegex(ValueError, 'missing'):
                await operation()
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(await self.app.projects.alist()), 1)

    async def test_lost_vision_tombstone_cannot_allow_image_id_reuse(self):
        from llm.components.vision import VisionComponent
        from tests.llm.test_vision import picture
        await self.create(VisionComponent())
        await self.data.aimport_image(picture(), title='original', identifier='record')
        await self.data.adelete('record')
        path = self.storage / 'records/record.json'
        path.unlink()
        before = self.snapshot()
        for operation in (lambda:self.data.alist(),
                          lambda:self.data.aimport_image(picture(), title='replacement', identifier='record'),
                          lambda:self.project.abackup(self.root / 'backup'), lambda:self.project.aclone()):
            with self.assertRaisesRegex(ValueError, 'missing'):
                await operation()
            self.assertEqual(self.snapshot(), before)

    async def test_record_semantics_are_checked_after_a_matching_manifest(self):
        import hashlib
        from llm.components.vision import VisionComponent
        from tests.llm.test_vision import picture
        await self.create(VisionComponent())
        await self.data.aimport_image(picture(), title='original', identifier='record')
        path = self.storage / 'records/record.json'
        original = json.loads(path.read_bytes())
        manifest_path = self.storage / self.component.RECORDS_MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        for version in (None, True, 1.0, '1', 2):
            with self.subTest(version=version):
                path.write_text(json.dumps({**original, 'format_version':version}))
                manifest['records']['record'] = hashlib.sha256(path.read_bytes()).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    await self.data.aload('record')
                self.assertEqual(self.snapshot(), before)

    async def test_manifest_write_failure_rolls_back_creation_update_and_deletion(self):
        from llm.components import storage as base
        for operation in ('create', 'save', 'delete'):
            with self.subTest(operation=operation):
                await self.create()
                if operation != 'create':
                    await self.data.acreate({'original':True}, identifier='record')
                before = self.snapshot()
                original = base.atomic_json
                def fail(path, value):
                    if path.name == self.component.RECORDS_MANIFEST:
                        raise OSError('manifest write failed')
                    return original(path, value)
                with patch.object(base, 'atomic_json', side_effect=fail), self.assertRaises(OSError):
                    if operation == 'create':
                        await self.data.acreate({'replacement':True}, identifier='record')
                    elif operation == 'save':
                        await self.data.asave('record', {'replacement':True})
                    else:
                        await self.data.adelete('record')
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(await self.data.alist(), {} if operation == 'create' else {'record':{'original':True}})

    async def test_process_death_preserves_the_record_and_manifest_commit_boundary(self):
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.components import storage as base
            from llm.components.base import Component
            from llm.services.infrastructure import storage
            from llm.llm import LargeLanguageModel
            class Notes(Component):
                name = directory = 'notes'
            root, operation, boundary = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
            write, unlink = base.atomic_json, storage.unlink_file
            def crash_write(path, value):
                write(path, value)
                if ((path.name == 'record.json' and boundary == 'record')
                        or (path.name == Component.RECORDS_MANIFEST and boundary == 'manifest')):
                    os._exit(77)
            def crash_unlink(path):
                unlink(path)
                if path.name == 'record.json' and boundary == 'record':
                    os._exit(77)
            async def main():
                async with LargeLanguageModel(root, components=[Notes()]) as app:
                    project = await app.projects.acreate(components=['notes'])
                    data = project.components.notes
                    if operation != 'create':
                        await data.acreate({'original':True}, identifier='record')
                    base.atomic_json, storage.unlink_file = crash_write, crash_unlink
                    if operation == 'create':
                        await data.acreate({'replacement':True}, identifier='record')
                    elif operation == 'save':
                        await data.asave('record', {'replacement':True})
                    else:
                        await data.adelete('record')
                    os._exit(77)
            asyncio.run(main())
        ''')
        for operation in ('create', 'save', 'delete'):
            for boundary in ('record', 'manifest', 'committed'):
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                    child = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
                        temporary, operation, boundary, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    try:
                        _, error = await asyncio.wait_for(child.communicate(), 20)
                    except BaseException:
                        if child.returncode is None:
                            child.kill()
                        await child.wait()
                        raise
                    self.assertEqual(child.returncode, 77, error.decode(errors='replace'))
                    async with LargeLanguageModel(temporary, components=[Notes()]) as app:
                        project = (await app.projects.alist())[0]
                        expected = {} if operation == 'create' else {'record':{'original':True}}
                        if boundary == 'committed':
                            expected = {} if operation == 'delete' else {'record':{'replacement':True}}
                        self.assertEqual(await project.components.notes.alist(), expected)
