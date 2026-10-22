"""Lost or incomplete output evidence cannot become empty or finalized results."""
from pathlib import Path
import asyncio
from copy import deepcopy
import json
import sys
import tempfile
import textwrap
import unittest
from llm.core.results import EngineDelta, EngineOutput
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.journal import OutputJournal
from tests.llm import test_engine_outputs as fixture

class OutputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'outputs.jsonl'
        self.journal = OutputJournal()
        self.journal.initialize(self.path)
        self.journal.append(self.path, [EngineDelta('answer', 'confirmed text', sequence=1)])

    def test_missing_newline_is_not_an_empty_output(self):
        self.path.write_bytes(self.path.read_bytes()[:-1])
        before = self.path.read_bytes()
        rejected = False
        try:
            self.journal.read(self.path)
        except ValueError:
            rejected = True
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(rejected)

    def test_append_preserves_unexplained_tail_bytes(self):
        self.path.write_bytes(self.path.read_bytes()[:-1])
        before = self.path.read_bytes()
        try:
            self.journal.append(self.path, [EngineDelta('answer', 'later', sequence=2)])
        except ValueError:
            pass
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_final_cannot_become_confirmed_output(self):
        value = EngineOutput('answer', 'partial text', final=False).to_dict()
        del value['final']
        with self.assertRaises(ValueError):
            EngineOutput.from_dict(value)

    def test_all_serialized_fields_are_required_and_explicit_empty_values_survive(self):
        for original in (EngineDelta('answer', '', sequence=1),
                         EngineOutput('answer', '', data=None, metadata={}, final=False)):
            codec, value = type(original), original.to_dict()
            self.assertEqual(codec.from_dict(value), original)
            for field in value:
                with self.subTest(codec=codec.__name__, field=field), self.assertRaises(ValueError):
                    codec.from_dict({key: item for key, item in value.items() if key != field})

    def test_missing_manifest_cannot_be_recreated_by_read_or_append(self):
        manifest = self.path.with_suffix('.manifest.json')
        before = self.path.read_bytes()
        manifest.unlink()
        for operation in (lambda: self.journal.read(self.path),
                          lambda: self.journal.append(self.path, [EngineDelta('answer', 'next', sequence=2)])):
            with self.assertRaises(FileNotFoundError):
                operation()
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(manifest.exists())

    def test_manifest_current_fields_types_and_source_values_are_required(self):
        path = self.path.with_suffix('.manifest.json')
        original = json.loads(path.read_bytes())
        changes = [{key: value for key, value in original.items() if key != field} for field in original]
        changes += [{**original, field: value} for field in ('schema_version', 'bytes', 'sequence')
                    for value in (None, True, 1.0, '1', -1)]
        changes += [{**original, 'sha256': value} for value in (None, '', '0' * 64)]
        changes += [{**original, 'sequence': 0}, {**original, 'bytes': 0}, {**original, 'extra': None}]
        source = self.path.read_bytes()
        for value in changes:
            with self.subTest(value=value):
                path.write_text(json.dumps(value))
                damaged = path.read_bytes()
                with self.assertRaises(ValueError):
                    self.journal.read(self.path)
                self.assertEqual((self.path.read_bytes(), path.read_bytes()), (source, damaged))

    def test_lost_complete_tail_record_is_not_a_shorter_successful_journal(self):
        original = self.path.read_bytes()
        self.journal.append(self.path, [EngineDelta('answer', 'later', sequence=2)])
        self.path.write_bytes(original)
        with self.assertRaises(ValueError):
            self.journal.read(self.path)
        with self.assertRaises(ValueError):
            self.journal.append(self.path, [EngineDelta('answer', 'replacement', sequence=2)])
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_prefix_does_not_use_or_rebuild_a_cached_index(self):
        self.journal.index_stride = 2
        self.journal.append(self.path, [EngineDelta('answer', 'second', sequence=2),
                                        EngineDelta('answer', 'third', sequence=3)])
        self.journal.read(self.path, after=2)
        index = self.path.with_name('outputs.index.json')
        cached = index.read_bytes()
        self.path.write_bytes(self.path.read_bytes().replace(b'confirmed text', b'corrupted text'))
        damaged = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.journal.read(self.path, after=2)
        self.assertEqual((self.path.read_bytes(), index.read_bytes()), (damaged, cached))

    def test_invalid_serialized_value_is_rejected_before_any_append(self):
        class BrokenDelta(EngineDelta):
            def to_dict(self):
                value = super().to_dict()
                del value['operation']
                return value
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.journal.append(self.path, [BrokenDelta('answer', 'broken', sequence=2)])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_index_structure_is_rebuilt_from_verified_source(self):
        self.journal.read(self.path, after=1)
        path = self.path.with_name('outputs.index.json')
        original = json.loads(path.read_bytes())['index']
        changes = [{'version': True}, {'size': False}, {'sequence': '1'},
                   {'anchors': None}, {'anchors': [[1, -1]]}, {'anchors': [[1, 0], [1, 0]]}]
        source = self.path.read_bytes()
        for change in changes:
            with self.subTest(change=change):
                value = {**deepcopy(original), **change}
                path.write_text(json.dumps({'index': value, 'checksum': self.journal._digest(value)}))
                self.assertEqual(self.journal.read(self.path, after=1), [])
                self.assertEqual(json.loads(path.read_bytes())['index'], original)
                self.assertEqual(self.path.read_bytes(), source)


class OutputPersistenceIntegrityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.OutputIntegrationTests.asyncSetUp
    backend = fixture.OutputIntegrationTests.backend
    request = fixture.OutputIntegrationTests.request

    async def lose_file(self, *, manifest):
        async def work(context):
            yield 'persisted'
        engine = BaseEngine(action=work)
        await self.backend(engine)
        run = await self.request()
        project_id, session_id, run_id = self.project.id, self.session.id, run.id
        path = run.data.paths.state / ('outputs.manifest.json' if manifest else 'outputs.jsonl')
        await self.app.shutdown()
        path.unlink()
        async with LargeLanguageModel(self.root, engines={'test': engine}) as app:
            session = await (await app.projects.aload(project_id)).sessions.aload(session_id)
            with self.assertRaises(FileNotFoundError):
                await session.run.aload(run_id)
        self.assertFalse(path.exists())

    async def test_reopen_does_not_recreate_missing_source(self):
        await self.lose_file(manifest=False)

    async def test_reopen_does_not_recreate_missing_manifest(self):
        await self.lose_file(manifest=True)

    async def test_process_death_preserves_the_last_committed_output_and_conversation(self):
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.engines.base import BaseEngine
            from llm.llm import LargeLanguageModel
            from llm.services.infrastructure import journal
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            append, save = journal.append_bytes, journal.atomic_json
            def crash_append(path, data):
                append(path, data)
                if boundary == 'source' and b'second' in data:
                    os._exit(77)
            def crash_manifest(path, value):
                save(path, value)
                if boundary == 'manifest' and path.name == 'outputs.manifest.json' and value['sequence'] == 2:
                    os._exit(77)
            journal.append_bytes, journal.atomic_json = crash_append, crash_manifest
            async def work(context):
                for text in ('first', 'second'):
                    with (root / 'emissions.txt').open('a') as stream:
                        stream.write(text + '\\n')
                    yield text
            async def main():
                async with LargeLanguageModel(root, engines={'test': BaseEngine(action=work)}) as app:
                    project = await app.projects.acreate()
                    await project.agents.acreate({'engine': 'test'}, identifier='test')
                    session = await project.sessions.acreate()
                    await (await session.run.submit('output', agent_id='test')).wait()
            asyncio.run(main())
        ''')
        for boundary in ('source', 'manifest'):
            with self.subTest(boundary=boundary):
                root = self.root / boundary
                child = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(root), boundary,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, error = await asyncio.wait_for(child.communicate(), 20)
                self.assertEqual(child.returncode, 77, error.decode(errors='replace'))
                async def unexpected(context):
                    self.fail('Interrupted output must not be replayed automatically')
                async with LargeLanguageModel(root, engines={'test': BaseEngine(action=unexpected)}) as app:
                    session = (await (await app.projects.alist())[0].sessions.alist())[0]
                    await session.run.start()
                    await session.run.wait_idle()
                    run = (await session.run.alist())[0]
                    self.assertEqual(run.data.status, 'interrupted')
                    events = await run.aoutput_events()
                    self.assertEqual([(value.sequence, value.text) for value in events], [(1, 'first')])
                    self.assertEqual((await run.aresponse()).content, 'first')
                    self.assertEqual((root / 'emissions.txt').read_text(), 'first\nsecond\n')
