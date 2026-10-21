"""Lost Memory request/cursor proof must not release original data or replay work."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from tests.llm import test_large_data as large_data
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from llm.components.memory.compaction import Compactions
from llm.services.infrastructure.storage import revision_token


class CompactionLossTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = large_data.LargeDataTests.asyncSetUp
    open = large_data.LargeDataTests.open
    run_loop = large_data.LargeDataTests.run_loop
    history = large_data.LargeDataTests.history
    options = large_data.LargeDataTests.options
    request = large_data.LargeDataTests.request
    tool_config = large_data.LargeDataTests.tool_config

    def auxiliary(self, **request):
        with (self.root / 'summary-effects').open('a') as stream:
            stream.write('called\n')
        yield from large_data.LargeDataTests.auxiliary(self, **request)

    async def test_lost_pending_request_is_not_empty_context(self):
        await self.history()
        await self.request()
        paths = list((self.project.paths.root / 'memory' / 'compactions').glob('*/*.json'))
        self.assertEqual(len(paths), 1)
        paths[0].unlink()
        with self.assertRaises((ValueError, OSError)):
            await self.memory.aget_context(self.session.id)
        self.assertFalse(paths[0].exists())

    async def test_lost_tool_reference_does_not_allow_original_file_deletion(self):
        await self.tool_config(mode='reference')
        model = ScriptedCompletion([chunk(calls=[call('{}', name='source_read')], finish='tool_calls')], large_data.answer())
        run, _ = await self.run_loop(model=model)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        saved = (await self.memory.alist_tool_summaries(self.session.id))[0]
        request = saved['request']
        reference = request['reference']
        source_paths = list(self.project.paths.root.rglob(Path(reference['path']).name))
        self.assertEqual(len(source_paths), 1)
        source, original = source_paths[0], source_paths[0].read_bytes()
        path = self.project.paths.root / 'memory' / 'tool_summaries' / self.session.id / (request['id'] + '.json')
        path.unlink()
        snapshot = await self.files.asnapshot('results')
        rejected = False
        try:
            await self.files.adelete_file('results', reference['path'], expected_revision=snapshot['version'])
        except (ValueError, OSError):
            rejected = True
        self.assertTrue(source.is_file(), 'lost summary reference allowed deletion of the original Tool source')
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(rejected)

    async def test_lost_completed_cursor_cannot_repeat_a_saved_model_effect(self):
        await self.history()
        await self.request()
        await self.run_loop()
        state = await self.memory.aget_context(self.session.id)
        self.assertEqual(state['request']['status'], 'partial')
        self.assertGreater(state['request']['checkpoint']['offset'], 0)
        self.assertEqual((self.root / 'summary-effects').read_text(), 'called\n')
        path, = (self.project.paths.root / 'memory' / 'compactions').glob('*/*.json')
        value = json.loads(path.read_bytes())
        del value['request']['checkpoint']
        path.write_text(json.dumps(value))
        try:
            state = await self.memory.aget_context(self.session.id)
            await self.memory.aresume_compaction(self.session.id, expected_revision=state['revision'])
            await self.run_loop()
        except (ValueError, OSError):
            pass
        self.assertEqual((self.root / 'summary-effects').read_text(), 'called\n',
                         'missing cursor repeated a previously completed model fragment')

    def snapshot(self):
        root = self.project.paths.root / 'memory'
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    async def reference(self, identifier='a' * 64):
        return await self.memory._async_call(self.memory._prepare_tool_summary, self.session.id,
            identifier, self.source.content, {}, {'component': 'data', 'identifier': 'results'}, 'reference', 'fixture-call')

    async def test_declared_loss_blocks_mutation_reference_release_backup_and_clone(self):
        await self.history()
        await self.request()
        reference = (await self.reference())['request']['reference']
        source, = self.project.paths.root.rglob(Path(reference['path']).name)
        original_source = source.read_bytes()
        root = self.project.paths.root / 'memory'
        saved = self.root / 'saved-memory'
        shutil.copytree(root, saved)
        paths = [root / Compactions.MANIFEST, root / Compactions.CONTEXTS, root / Compactions.TOOLS,
                 *sorted((root / Compactions.CONTEXTS).glob('*/*.json')),
                 *sorted((root / Compactions.TOOLS).glob('*/*.json'))]
        for path in paths:
            with self.subTest(path=str(path.relative_to(root))):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                before = self.snapshot()
                snapshot = await self.files.asnapshot('results')
                for operation in (lambda: self.memory.aget_context(self.session.id),
                                  lambda: self.memory.alist_tool_summaries(self.session.id),
                                  lambda: self.reference('b' * 64),
                                  lambda: self.files.adelete_file('results', reference['path'], expected_revision=snapshot['version']),
                                  lambda: self.project.abackup(self.root / 'backup'),
                                  lambda: self.project.aclone()):
                    with self.assertRaises((ValueError, OSError)):
                        await operation()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(source.read_bytes(), original_source)
                self.assertFalse(path.exists())
                self.assertFalse((self.root / 'backup').exists())
                self.assertEqual(len(await self.app.projects.alist()), 1)
                shutil.rmtree(root)
                shutil.copytree(saved, root)

    async def test_disabled_owner_preserves_references_and_never_reinitializes_loss(self):
        request = (await self.reference())['request']
        root = self.project.paths.root / 'memory'
        path = root / Compactions.key(self.session.id, None, request['id'])
        source, = self.project.paths.root.rglob(Path(request['reference']['path']).name)
        original = source.read_bytes()
        await self.project.components.aremove('memory')
        path.unlink()
        before = self.snapshot()
        for operation in (lambda: self.project.components.aselect(['memory', 'data', 'source']),
                          lambda: self.project.abackup(self.root / 'backup'),
                          lambda: self.project.components.aremove('memory', permanent=True),
                          lambda: self.files.adelete('results')):
            with self.assertRaises((ValueError, OSError)):
                await operation()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse(path.exists())

    async def test_manifest_fields_ownership_scope_and_regular_paths_are_required(self):
        await self.history()
        root = self.project.paths.root / 'memory'
        manifest_path = root / Compactions.MANIFEST
        original = json.loads(manifest_path.read_bytes())
        key, = original['records']
        cases = [{k: v for k, v in original.items() if k != name} for name in original]
        cases += [{**original, 'schema_version': value} for value in (None, True, 2, '1')]
        cases += [{**original, 'project_id': 'other'}, {**original, 'component': 'other'},
                  {**original, 'records': {}}, {**original, 'records': None},
                  {**original, 'records': {key: None}},
                  {**original, 'records': {key: {**original['records'][key], 'scope': 'other'}}},
                  {**original, 'records': {key: {**original['records'][key], 'revision': None}}}]
        for value in cases:
            with self.subTest(value=value):
                manifest_path.write_text(json.dumps(value))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    await self.memory.aget_context(self.session.id)
                self.assertEqual(self.snapshot(), before)
        manifest_path.write_text(json.dumps(original))
        for path in (manifest_path, root / key):
            content = path.read_bytes()
            for kind in ('link', 'fifo', 'directory'):
                with self.subTest(path=path.name, kind=kind):
                    path.unlink()
                    target = self.root / 'outside.json'
                    target.write_bytes(content)
                    if kind == 'link':
                        path.symlink_to(target)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.mkdir()
                    with self.assertRaises(ValueError):
                        await self.memory.aget_context(self.session.id)
                    self.assertEqual(target.read_bytes(), content)
                    path.rmdir() if kind == 'directory' else path.unlink()
                    path.write_bytes(content)

    async def test_matching_hash_cannot_default_context_request_cursor_or_reference(self):
        await self.history()
        await self.request()
        await self.run_loop()
        await self.reference()
        root = self.project.paths.root / 'memory'
        manifest_path = root / Compactions.MANIFEST
        manifest = json.loads(manifest_path.read_bytes())
        for key in sorted(manifest['records']):
            path = root / key
            original = json.loads(path.read_bytes())
            request = original['request']
            cases = [None, {}, {**original, 'request': None}]
            fields = ('id', 'status', 'options', 'sources', 'stored_sources', 'checkpoint') if key.startswith(Compactions.CONTEXTS) else (
                'id', 'status', 'options', 'reference', 'mode', 'tool_call_id')
            cases += [{**original, 'request': {k: v for k, v in request.items() if k != field}} for field in fields]
            cases += [{**original, 'request': {**request, 'status': None}},
                      {**original, 'request': {**request, 'options': None}}]
            if key.startswith(Compactions.CONTEXTS):
                cases += [{k: v for k, v in original.items() if k != 'view'},
                          {**original, 'view': {**original['view'], 'scope': 'other'}},
                          {**original, 'view': {**original['view'], 'iteration': True}}]
                cases += [{**original, 'request': {**request, 'options': {**request['options'], 'completion': completion}}}
                          for completion in ({}, {'model': 'test', 'stream': False},
                                             {'model': 'test', 'n': 2}, {'model': 'test', 'messages': []})]
            else:
                cases += [{**original, 'request': {**request, 'id': 'other'}},
                          {**original, 'request': {**request, 'reference': None}},
                          {**original, 'applied': None}]
            for value in cases:
                with self.subTest(key=key, value=value):
                    path.write_text(json.dumps(value))
                    manifest['records'][key]['revision'] = revision_token(value)
                    manifest_path.write_text(json.dumps(manifest))
                    before = self.snapshot()
                    for operation in (lambda: self.memory.aget_context(self.session.id),
                                      lambda: self.memory.alist_tool_summaries(self.session.id),
                                      lambda: self.files.adelete('results')):
                        with self.assertRaises(ValueError):
                            await operation()
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual((self.root / 'summary-effects').read_text(), 'called\n')
            path.write_text(json.dumps(original))
            manifest['records'][key]['revision'] = revision_token(original)
            manifest_path.write_text(json.dumps(manifest))

    async def test_write_and_clear_failures_restore_state_source_and_inventory(self):
        from llm.components.memory import compaction
        await self.history()
        await self.request()
        root = self.project.paths.root / 'memory'
        write, unlink = compaction.atomic_json, compaction.unlink_file
        for operation in ('cancel', 'reference', 'delete'):
            if operation == 'delete':
                view = await self.memory.aget_context(self.session.id)
                await self.memory.acancel_compaction(self.session.id, expected_revision=view['revision'])
            for boundary in ('record', 'manifest'):
                with self.subTest(operation=operation, boundary=boundary):
                    before = self.snapshot()
                    original_files = await self.files.asnapshot('results')
                    view = await self.memory.aget_context(self.session.id)
                    def fail_write(path, value):
                        write(path, value)
                        if (path.name == Compactions.MANIFEST) == (boundary == 'manifest'):
                            raise OSError('failed after compaction flush')
                    def fail_unlink(path):
                        unlink(path)
                        if boundary == 'record':
                            raise OSError('failed after compaction unlink')
                    with patch.object(compaction, 'atomic_json', side_effect=fail_write), \
                         patch.object(compaction, 'unlink_file', side_effect=fail_unlink), self.assertRaises(OSError):
                        if operation == 'cancel':
                            await self.memory.acancel_compaction(self.session.id, expected_revision=view['revision'])
                        elif operation == 'reference':
                            await self.reference()
                        else:
                            await self.memory.adelete_compaction(self.session.id, expected_revision=view['revision'])
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(await self.files.asnapshot('results'), original_files)
                    self.assertEqual(await self.memory.alist_tool_summaries(self.session.id), [])
                    self.assertEqual(await self.memory.aget_context(self.session.id), view)

    async def test_process_death_recovers_context_and_source_reference_commit_boundaries(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.llm import LargeLanguageModel
            from llm.components.memory import MemoryComponent, compaction
            from llm.components.data import DataComponent
            directory, operation, boundary = sys.argv[1:]
            root = Path(directory)
            async def main():
                component = MemoryComponent()
                async with LargeLanguageModel(root / 'workspace', components=[component, DataComponent()], engines={}) as app:
                    project = await app.projects.acreate(components=['memory', 'data'])
                    session = await project.sessions.acreate()
                    memory = project.components.memory
                    files = await project.components.aget('data')
                    await files.acreate({'title': 'Source storage'}, identifier='results')
                    view = {'run_id': 'fixture', 'scope': None, 'iteration': 0, 'tokens': None,
                            'max_tokens': None, 'reserve_tokens': None, 'turns': []}
                    if operation in ('update', 'delete'):
                        await memory._async_call(memory._observe_context, session.id, None, view)
                    old = await memory.aget_context(session.id)
                    project_root = project.paths.root
                    def snapshot():
                        return {str(p.relative_to(project_root)): p.read_bytes().hex()
                                for directory in ('memory', 'data') for p in (project_root / directory).rglob('*') if p.is_file()}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'session_id': session.id,
                        'before': snapshot(), 'old': old}))
                    write, unlink = compaction.atomic_json, compaction.unlink_file
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    def crash_write(path, value):
                        if boundary == 'source' and path.name != compaction.Compactions.MANIFEST:
                            crash('source')
                        write(path, value)
                        if boundary == 'record' and path.name != compaction.Compactions.MANIFEST:
                            crash('record')
                        if boundary == 'manifest' and path.name == compaction.Compactions.MANIFEST:
                            crash('manifest')
                    def crash_unlink(path):
                        unlink(path)
                        if boundary == 'record':
                            crash('record')
                    compaction.atomic_json, compaction.unlink_file = crash_write, crash_unlink
                    if operation == 'tool_source':
                        await memory._async_call(memory._prepare_tool_summary, session.id, 'a' * 64,
                            'original source', {}, {'component': 'data', 'identifier': 'results'}, 'reference', 'fixture-call')
                    elif operation == 'delete':
                        await memory.adelete_compaction(session.id, expected_revision=old['revision'])
                    else:
                        await memory._async_call(memory._observe_context, session.id, None, {**view, 'iteration': 1})
                    crash('committed')
            asyncio.run(main())
        ''')
        for operation in ('create', 'update', 'delete', 'tool_source'):
            boundaries = ('source', 'record', 'manifest', 'committed') if operation == 'tool_source' else ('record', 'manifest', 'committed')
            for boundary in boundaries:
                with self.subTest(operation=operation, boundary=boundary), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    child = subprocess.run([sys.executable, '-c', code, directory, operation, boundary],
                                           timeout=20, capture_output=True, text=True)
                    self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                    self.assertEqual((root / 'reached').read_text(), boundary)
                    context = json.loads((root / 'context.json').read_text())
                    from llm.llm import LargeLanguageModel
                    from llm.components.memory import MemoryComponent
                    from llm.components.data import DataComponent
                    async with LargeLanguageModel(root / 'workspace', components=[MemoryComponent(), DataComponent()], engines={}) as app:
                        project = await app.projects.aload(context['project_id'])
                        memory = project.components.memory
                        value = await memory.aget_context(context['session_id'])
                        after = {str(p.relative_to(project.paths.root)): p.read_bytes().hex()
                                 for name in ('memory', 'data') for p in (project.paths.root / name).rglob('*') if p.is_file()}
                        files = await project.components.aget('data')
                        if boundary != 'committed':
                            self.assertEqual(after, context['before'])
                            self.assertEqual(value, context['old'])
                            self.assertEqual(await memory.alist_tool_summaries(context['session_id']), [])
                        elif operation == 'delete':
                            self.assertIsNone(value['context'])
                        elif operation == 'tool_source':
                            saved, = await memory.alist_tool_summaries(context['session_id'])
                            reference = saved['request']['reference']
                            result = await memory.aread_source(context['session_id'], saved['request']['id'],
                                expected_revision=reference['revision'], offset=0, limit=None)
                            self.assertEqual(result['text'], 'original source')
                            with self.assertRaises(ValueError):
                                await files.adelete('results')
                        else:
                            self.assertEqual(value['context']['iteration'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
