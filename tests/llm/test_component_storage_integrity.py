"""Independent probe; does not modify the frozen full-regression sources."""
import json
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import shutil
import sys
import textwrap
import unittest
from unittest.mock import patch

from llm.components.base import Component
from llm.components.memory import MemoryComponent
from llm.llm import LargeLanguageModel
from llm.providers.observations import ModelCall, observed_call
from llm.components.usage import ModelUsageRecord, ModelUsageStatus
from llm.core.models import now


class Meter(Component):
    name = directory = 'meter'


class ComponentUsageLossProbe(unittest.IsolatedAsyncioTestCase):
    async def test_lost_receipt_cannot_free_a_call_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            effect = root / 'effects.txt'
            invocation = ModelCall('custom', 'custom', lambda counter: 1,
                                   lambda response: {'total_tokens': 1}, lambda error: 'custom_error')
            async def call(**request):
                with effect.open('a') as stream:
                    stream.write('called\n')
                return {}
            async with LargeLanguageModel(root, components=[Meter()]) as app:
                project = await app.projects.acreate(components=['meter'],
                    config={'policies': {'usage': {'project_max_calls': 1}}})
                handle = app.project_manager.component(project.data, 'meter')
                with handle.model_scope():
                    await observed_call(invocation, {}, call)
                receipts = await project.components.meter.amodel_usage()
                path = project.paths.root / 'meter' / 'usage' / (receipts[0]['id'] + '.json')
                path.unlink()
                rejected = False
                try:
                    with handle.model_scope():
                        await observed_call(invocation, {}, call)
                except (ValueError, OSError):
                    rejected = True
                self.assertEqual((rejected, effect.read_text(), path.exists()),
                                 (True, 'called\n', False))

    async def test_completed_usage_requires_end_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            meter = Meter()
            async with LargeLanguageModel(temporary, components=[meter]) as app:
                project = await app.projects.acreate(components=['meter'])
                handle = app.project_manager.component(project.data, 'meter')
                invocation = ModelCall('custom', None, lambda counter: 0,
                    lambda response: {'total_tokens': 0}, lambda error: 'custom_error')
                async def call(**request):
                    return {}
                with handle.model_scope():
                    await observed_call(invocation, {}, call)
                receipt = (await project.components.meter.amodel_usage())[0]
                path = project.paths.root / 'meter' / 'usage' / (receipt['id'] + '.json')
                del receipt['ended_at']
                path.write_text(json.dumps(receipt))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    await project.amodel_usage()
                self.assertEqual(path.read_bytes(), before)


class ComponentRootLossProbe(unittest.IsolatedAsyncioTestCase):
    async def lost_root(self, disabled):
        with tempfile.TemporaryDirectory() as temporary:
            memory = MemoryComponent()
            async with LargeLanguageModel(temporary, components=[memory]) as app:
                project = await app.projects.acreate(components=['memory'])
                root = memory.root(project.data)
                identity = memory.identity(project.data)
                if disabled:
                    await project.components.aremove('memory')
                shutil.rmtree(root)
                rejected = False
                try:
                    app.project_manager.set_components(project.data, ('memory',))
                except (ValueError, OSError):
                    rejected = True
                changed = root.exists() and memory.identity(project.data) != identity
                self.assertEqual((rejected, root.exists(), changed), (True, False, False))

    async def test_selected_root_loss_is_not_first_creation(self):
        await self.lost_root(False)

    async def test_disabled_root_loss_is_not_first_creation(self):
        await self.lost_root(True)


class ComponentReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, *, selected=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.effect = self.root / 'effects.txt'
        self.meter = Meter()
        self.app = LargeLanguageModel(self.root, components=[self.meter])
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=['meter'] if selected else [],
            config={'policies': {'usage': {'project_max_calls': 1}}})
        if selected:
            self.handle = self.app.project_manager.component(self.project.data, 'meter')
        self.invocation = ModelCall('custom', None, lambda counter: 0,
            lambda response: {'total_tokens': 0}, lambda error: 'custom_error')

    async def call(self):
        async def effect(**request):
            with self.effect.open('a') as stream:
                stream.write('called\n')
            return {}
        with self.handle.model_scope():
            return await observed_call(self.invocation, {}, effect)

    def usage_root(self):
        return self.project.paths.root / 'meter' / 'usage'

    async def test_never_selected_storage_is_empty_without_being_created(self):
        await self.create(selected=False)
        self.assertEqual((await self.project.amodel_usage())['call_count'], 0)
        self.assertFalse((self.project.paths.root / 'meter').exists())

    async def test_empty_storage_and_explicit_null_and_zero_receipts_are_valid(self):
        await self.create()
        self.assertEqual(await self.project.components.meter.amodel_usage(), [])
        await self.call()
        record = (await self.project.components.meter.amodel_usage())[0]
        self.assertEqual((record['model'], record['session_id'], record['run_id'], record['reserved_tokens'],
                          record['usage'], record['usage_complete'], record['error'], record['diagnostic_code']),
                         (None, None, None, None, {'total_tokens': 0}, True, None, None))
        self.assertEqual(record['project_id'], self.project.id)

    async def test_missing_manifest_or_usage_directory_is_not_reinitialized(self):
        for whole_directory in (False, True):
            with self.subTest(whole_directory=whole_directory):
                await self.create()
                await self.call()
                root = self.usage_root()
                if whole_directory:
                    shutil.rmtree(root)
                else:
                    (root / 'manifest.json').unlink()
                before = {p.name:p.read_bytes() for p in root.glob('*')}
                await self.project.components.aremove('meter')
                for operation in (lambda: self.project.amodel_usage(),
                                  lambda: self.project.components.aselect(['meter']),
                                  lambda: self.project.components.aremove('meter', permanent=True),
                                  lambda: self.project.abackup(self.root / 'backup')):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                self.assertEqual({p.name:p.read_bytes() for p in root.glob('*')}, before)
                self.assertEqual(self.effect.read_text(), 'called\n')

    async def test_manifest_fields_owner_revisions_and_inventory_are_required(self):
        await self.create()
        await self.call()
        path = self.usage_root() / 'manifest.json'
        original = json.loads(path.read_bytes())
        changes = [{key:value for key,value in original.items() if key != field} for field in original]
        changes += [{**original, 'schema_version': value} for value in (True, None, '1', 1.0, 2)]
        changes += [{**original, key: value} for key,value in (
            ('project_id','another'), ('component','another'), ('records',None), ('records',{}))]
        identifier = next(iter(original['records']))
        changes += [{**original,'records':{identifier:'0'*64}}]
        for value in changes:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                with self.assertRaises((ValueError, OSError)):
                    await self.project.amodel_usage()
                self.assertEqual(path.read_bytes(), before)

    async def test_completed_receipt_cannot_be_overwritten_or_reopened(self):
        await self.create()
        await self.call()
        original = (await self.project.components.meter.amodel_usage())[0]
        path = self.usage_root() / (original['id'] + '.json')
        before = path.read_bytes()
        for value in (original, {**original, 'status': ModelUsageStatus.STARTED, 'ended_at': None,
                                'usage': {}, 'usage_complete': False}):
            with self.app.project_manager.ownership.scope(), self.assertRaises(ValueError):
                self.meter.save_model_usage(self.project.data, value)
            self.assertEqual(path.read_bytes(), before)

    async def test_failed_completion_manifest_write_keeps_reservation_and_quota(self):
        await self.create()
        from llm.components import base
        original = base.atomic_json
        terminal = False
        def fail(path, value):
            nonlocal terminal
            if value.get('status') == ModelUsageStatus.COMPLETED:
                terminal = True
            if terminal and path.name == 'manifest.json':
                raise OSError('manifest unavailable')
            return original(path, value)
        with patch.object(base, 'atomic_json', fail), self.assertRaises(OSError):
            await self.call()
        self.assertEqual((await self.project.components.meter.amodel_usage())[0]['status'], 'started')
        with self.assertRaisesRegex(Exception, 'exhausted'):
            await self.call()
        self.assertEqual(self.effect.read_text(), 'called\n')

    async def test_unavailable_disabled_owner_cannot_disappear_from_quota(self):
        await self.create()
        await self.call()
        await self.project.components.aremove('meter')
        project_id = self.project.id
        await self.app.shutdown()
        async with LargeLanguageModel(self.root, components=[]) as reopened:
            project = await reopened.projects.aload(project_id)
            with self.assertRaisesRegex(ValueError, 'Unavailable'):
                await project.amodel_usage()
            with self.assertRaisesRegex(ValueError, 'Unavailable'):
                await project.abackup(self.root / 'backup')
        self.assertEqual(self.effect.read_text(), 'called\n')

    async def test_project_storage_declaration_is_required_and_not_editable(self):
        await self.create()
        path = self.project.paths.root / 'project.json'
        original = json.loads(path.read_bytes())
        changes = [{key:value for key,value in original.items() if key != 'initialized_components'}]
        changes += [{**original,'initialized_components':value} for value in (None, {}, [], ['meter','meter'], [True])]
        for value in changes:
            path.write_text(json.dumps(value))
            before = path.read_bytes()
            with self.assertRaises((ValueError, TypeError)):
                await self.app.projects.aload(self.project.id)
            self.assertEqual(path.read_bytes(), before)
        path.write_text(json.dumps(original))
        changed = deepcopy(self.project.data)
        changed.initialized_components = ()
        with self.assertRaisesRegex(ValueError, 'managed'):
            self.app.project_manager.save(changed)

    async def test_process_death_respects_reservation_and_completion_commit_boundaries(self):
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.components import base
            from llm.components.base import Component
            from llm.llm import LargeLanguageModel
            from llm.providers.observations import ModelCall, observed_call
            class Meter(Component):
                name = directory = 'meter'
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            original, terminal = base.atomic_json, False
            def crash(path, value):
                global terminal
                original(path, value)
                if value.get('status') == 'started' and boundary == 'reservation':
                    os._exit(77)
                if value.get('status') == 'completed':
                    terminal = True
                    if boundary == 'receipt':
                        os._exit(77)
                if terminal and path.name == 'manifest.json' and boundary == 'manifest':
                    os._exit(77)
            async def main():
                async with LargeLanguageModel(root, components=[Meter()]) as app:
                    project = await app.projects.acreate(components=['meter'],
                        config={'policies':{'usage':{'project_max_calls':1}}})
                    handle = app.project_manager.component(project.data, 'meter')
                    base.atomic_json = crash
                    async def effect(**request):
                        if boundary == 'before_effect':
                            os._exit(77)
                        with (root/'effects.txt').open('a') as stream:
                            stream.write('called\\n')
                        return {}
                    invocation = ModelCall('custom', None, lambda counter: 0,
                        lambda response: {'total_tokens':0}, lambda error:'custom_error')
                    with handle.model_scope():
                        await observed_call(invocation, {}, effect)
            asyncio.run(main())
        ''')
        for boundary in ('reservation', 'before_effect', 'receipt', 'manifest'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                self.root, self.effect = Path(temporary), Path(temporary)/'effects.txt'
                child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, temporary, boundary,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    _, error = await asyncio.wait_for(child.communicate(), 20)
                except BaseException:
                    if child.returncode is None:
                        child.kill()
                    await child.wait()
                    raise
                self.assertEqual(child.returncode, 77, error.decode(errors='replace'))
                async with LargeLanguageModel(self.root, components=[Meter()]) as app:
                    project = (await app.projects.alist())[0]
                    records = await project.components.meter.amodel_usage()
                    self.assertEqual([r['status'] for r in records], [] if boundary == 'reservation' else ['started'])
                    self.assertEqual(self.effect.read_text() if self.effect.exists() else '',
                                     'called\n' if boundary in ('receipt', 'manifest') else '')
                    self.handle = app.project_manager.component(project.data, 'meter')
                    self.invocation = ModelCall('custom', None, lambda counter: 0,
                        lambda response: {'total_tokens':0}, lambda error:'custom_error')
                    if boundary == 'reservation':
                        await self.call()
                    else:
                        with self.assertRaisesRegex(Exception, 'exhausted'):
                            await self.call()
                    self.assertEqual(self.effect.read_text() if self.effect.exists() else '',
                                     '' if boundary == 'before_effect' else 'called\n')


class ComponentUsageRecordTests(unittest.TestCase):
    def record(self):
        return ModelUsageRecord(schema_version=ModelUsageRecord.SCHEMA_VERSION,
            id='receipt', project_id='project', component='meter', operation='custom', model=None,
            session_id=None, run_id=None, started_at=now(), ended_at=None, status=ModelUsageStatus.STARTED,
            usage={}, usage_complete=False, reserved_tokens=0, error=None, diagnostic_code=None).to_dict()

    def test_current_fields_types_and_state_evidence_are_required_independently_of_manifest(self):
        original = self.record()
        self.assertEqual(ModelUsageRecord.from_dict(original).to_dict(), original)
        invalid = [{key:value for key,value in original.items() if key != field} for field in original]
        invalid += [{**original,key:value} for key,value in (
            ('schema_version',True), ('id','manifest'), ('status',None), ('started_at',None),
            ('started_at','2000-01-01T00:00:00'), ('ended_at',now()), ('usage_complete',0),
            ('usage',None), ('usage',{'total_tokens':0}), ('reserved_tokens',True), ('reserved_tokens',-1),
            ('run_id','unpaired'), ('error','unexpected'), ('model',0))]
        completed = {**original,'ended_at':now(),'status':ModelUsageStatus.COMPLETED,
                     'usage_complete':True,'usage':{'total_tokens':0}}
        self.assertEqual(ModelUsageRecord.from_dict(completed).usage, {'total_tokens':0})
        invalid += [{**completed,key:value} for key,value in (
            ('ended_at',None), ('ended_at','2000-01-01T00:00:00+00:00'),
            ('usage',{'total_tokens':True}), ('usage',{'total_tokens':-1}), ('diagnostic_code','unexpected'),
            ('status',ModelUsageStatus.FAILED))]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                ModelUsageRecord.from_dict(value)

    def test_finishing_reservation_requires_unchanged_owner_and_reserved_amount(self):
        value = self.record()
        previous = ModelUsageRecord.from_dict(value)
        terminal = {**value,'ended_at':now(),'status':ModelUsageStatus.COMPLETED}
        ModelUsageRecord.from_dict(terminal).validate_update(previous)
        for key,replacement in (('id','other'), ('project_id','other'), ('component','other'),
                                ('operation','other'), ('model','other'), ('reserved_tokens',None)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ModelUsageRecord.from_dict({**terminal,key:replacement}).validate_update(previous)
