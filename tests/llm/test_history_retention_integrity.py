"""Retention must keep damaged child evidence, just like direct parent deletion."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from unittest.mock import patch

from llm.services.infrastructure.storage import tree_snapshot
from tests.llm import test_operations_schema as fixtures


class HistoryRetentionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    app = fixtures.OperationsSchemaTests.app
    completed = fixtures.OperationsSchemaTests.completed

    @staticmethod
    def originals(session):
        root = session.paths.root
        return tree_snapshot(root, exclude={str(path.relative_to(root)) for path in root.rglob("*")
                                            if "logs" in path.relative_to(root).parts})

    async def damaged_step(self, *, missing):
        app, model = await self.app()
        project, session, runs = await self.completed(app)
        run = runs[0]
        step = (await run.steps.alist())[0]
        path = step.paths.root / 'step.json'
        self.assertTrue(path.is_file())
        if missing:
            path.unlink()
        else:
            path.write_bytes(b'{damaged required Step')
        before = self.originals(session)
        effects = len(model.requests)
        rejected = False
        try:
            plan = await project.aretention_plan('run')
            await project.aprune_history('run', [{'session_id': session.id, 'run_id': run.id}], expected_version=plan.version)
        except (ValueError, OSError):
            rejected = True
        self.assertEqual({'rejected': rejected,
                          'preserved': self.originals(session) == before,
                          'model_calls': len(model.requests)},
                         {'rejected': True, 'preserved': True, 'model_calls': effects})

    async def test_retention_cannot_discard_corrupt_step_evidence(self):
        await self.damaged_step(missing=False)

    async def test_retention_cannot_discard_missing_step_evidence(self):
        await self.damaged_step(missing=True)

    async def history(self, count=2):
        app, model = await self.app(responses=[[fixtures.chunk(str(i), finish='stop')] for i in range(count)])
        project, session, runs = await self.completed(app, count)
        return app, model, project, session, runs

    async def test_inspection_is_read_only_and_only_explicit_subset_is_pruned(self):
        app, model, project, session, runs = await self.history()
        before = self.originals(session)
        plan = await project.aretention_plan('run')
        self.assertEqual({row['run_id'] for row in plan.candidates}, {run.id for run in runs})
        self.assertEqual(self.originals(session), before)
        await project.aprune_history('run', [{'session_id': session.id, 'run_id': runs[0].id}],
                                     expected_version=plan.version)
        self.assertEqual([run.id for run in await session.run.alist()], [runs[1].id])
        self.assertEqual(len(await session.aconversation()), 2)
        self.assertEqual(len(model.requests), 2)
        self.assertEqual((await project.arecovery()).issues, [])

    async def test_raw_source_change_cannot_reuse_plan_with_preserved_size_and_mtime(self):
        app, model, project, session, runs = await self.history()
        artifact = runs[0].data.paths.root / 'artifacts/source'
        artifact.parent.mkdir()
        artifact.write_bytes(b'original')
        stamp = artifact.stat()
        plan = await project.aretention_plan('run')
        artifact.write_bytes(b'changed!')
        os.utime(artifact, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        before = self.originals(session)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            await project.aprune_history('run', [{'session_id': session.id, 'run_id': runs[0].id}],
                                         expected_version=plan.version)
        self.assertEqual(self.originals(session), before)
        self.assertEqual(len(model.requests), 2)

    async def test_invalid_duplicate_unknown_and_protected_targets_preserve_history(self):
        app, model, project, session, runs = await self.history()
        plan = await project.aretention_plan('run')
        target = {'session_id': session.id, 'run_id': runs[0].id}
        before = self.originals(session)
        for targets, version in (([target, target], plan.version), ([target], None),
                                 ([{'session_id': session.id}], plan.version),
                                 ([{**target, 'run_id': 'f' * 32}], plan.version),
                                 ([{**target, 'run_id': '../outside'}], plan.version),
                                 (target, plan.version)):
            with self.subTest(targets=targets, version=version), self.assertRaises(ValueError):
                await project.aprune_history('run', targets, expected_version=version)
            self.assertEqual(self.originals(session), before)
        await session.asave(metadata={'retain': True})
        plan = await project.aretention_plan('run')
        self.assertEqual(plan.candidates, [])
        before = self.originals(session)
        with self.assertRaisesRegex(ValueError, 'protected'):
            await project.aprune_history('run', [target], expected_version=plan.version)
        self.assertEqual(self.originals(session), before)
        self.assertEqual(len(model.requests), 2)

    async def test_estimation_is_explicit_and_never_changes_deletion_authority(self):
        app, model, project, session, runs = await self.history()
        calls = []
        plan = await project.aretention_plan('run', counter=lambda request: calls.append(request) or 7,
                                             counter_params={'model': 'selected/counter'})
        self.assertEqual(plan.tokens, 14)
        self.assertTrue(all(request['model'] == 'selected/counter' for request in calls))
        self.assertEqual(plan.version, (await project.aretention_plan('run')).version)
        with self.assertRaisesRegex(ValueError, 'override messages'):
            await project.aretention_plan('run', counter=lambda request: 0, counter_params={'messages': []})
        for invalid in (True, -1, 1.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                await project.aretention_plan('run', counter=lambda request: invalid)
        await project.aprune_history('run', [], expected_version=plan.version)
        self.assertEqual(len(model.requests), 2)

    async def test_failure_after_real_pruning_rolls_back_sources_and_conversation(self):
        app, model, project, session, runs = await self.history()
        plan = await project.aretention_plan('run')
        targets = [{'session_id': session.id, 'run_id': run.id} for run in runs]
        before = self.originals(session)
        delete = app.run_repository.delete_history
        calls = []
        def fail(*args):
            delete(*args)
            calls.append(args[-1])
            raise OSError('after real Run removal')
        with patch.object(app.run_repository, 'delete_history', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'real Run removal'):
                await project.aprune_history('run', targets, expected_version=plan.version)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.originals(session), before)
        self.assertEqual(len(model.requests), 2)

    async def test_concurrent_reviewed_requests_cannot_repeat_pruning(self):
        app, model, project, session, runs = await self.history()
        plan = await project.aretention_plan('run')
        target = {'session_id': session.id, 'run_id': runs[0].id}
        results = await asyncio.gather(*(project.aprune_history('run', [target], expected_version=plan.version)
                                        for _ in range(2)), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, list) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 1)
        self.assertEqual([run.id for run in await session.run.alist()], [runs[1].id])
        self.assertEqual(len(await session.aconversation()), 2)
        self.assertEqual(len(model.requests), 2)

    async def test_cancelled_request_drains_complete_selected_deletion(self):
        app, model, project, session, runs = await self.history()
        plan = await project.aretention_plan('run')
        targets = [{'session_id': session.id, 'run_id': run.id} for run in runs]
        delete = app.run_repository.delete_history
        entered, finish = threading.Event(), threading.Event()
        def paused(*args):
            delete(*args)
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('pruning was not released')
        with patch.object(app.run_repository, 'delete_history', side_effect=paused):
            task = asyncio.create_task(project.aprune_history('run', targets, expected_version=plan.version))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                task.cancel()
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(await session.run.alist(), [])
        self.assertEqual(await session.aconversation(), [])
        self.assertTrue(session.paths.root.exists())
        self.assertEqual(len(model.requests), 2)

    async def test_process_death_preserves_original_or_complete_selected_history(self):
        code = textwrap.dedent('''
            import asyncio, json, os, sys
            from pathlib import Path
            from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig
            from llm.services.history.conversation import ConversationStore
            from llm.services.infrastructure import transactions
            from llm.services.infrastructure.storage import tree_snapshot
            from tests.llm.test_loop import chunk
            root, boundary = Path(sys.argv[1]), sys.argv[2]
            def completion(**request):
                with (root / 'effects').open('a') as stream:
                    stream.write('called\\n')
                yield chunk('answer', finish='stop')
            async def main():
                async with LargeLanguageModel(root / 'workspace', engines={'custom': LoopEngine(completion_fn=completion)}) as app:
                    project = await app.projects.acreate(config=ProjectConfig(parameters={'engines': {
                        'custom': {'config': {'completion': {'model': 'test'}}}}}))
                    session = await project.sessions.acreate()
                    runs = []
                    for _ in range(3):
                        run = await (await session.run.submit('work', engine='custom')).wait()
                        assert run.data.status == 'completed', run.data.error
                        runs.append(run)
                    await session.run.shutdown()
                    plan = await project.aretention_plan('run')
                    targets = [{'session_id': session.id, 'run_id': run.id} for run in runs[:2]]
                    excluded = {str(p.relative_to(session.paths.root)) for p in session.paths.root.rglob('*')
                                if 'logs' in p.relative_to(session.paths.root).parts}
                    (root / 'context.json').write_text(json.dumps({'project_id': project.id, 'session_id': session.id,
                        'run_ids': [run.id for run in runs], 'before': tree_snapshot(session.paths.root, exclude=excluded)}))
                    def crash(label):
                        (root / 'reached').write_text(label)
                        os._exit(77)
                    prune, delete, write = ConversationStore.prune, app.run_repository.delete_history, transactions._write
                    count = 0
                    def cut_prune(*args):
                        prune(*args)
                        if boundary == 'prune':
                            crash('prune')
                    def cut_delete(*args):
                        nonlocal count
                        delete(*args)
                        count += 1
                        if boundary == 'remove' + str(count):
                            crash(boundary)
                    def cut_write(path, value):
                        write(path, value)
                        if boundary == 'marker' and path.name == 'COMMITTED':
                            crash('marker')
                    ConversationStore.prune = cut_prune
                    app.run_repository.delete_history, transactions._write = cut_delete, cut_write
                    await project.aprune_history('run', targets, expected_version=plan.version)
                    crash('committed')
            asyncio.run(main())
        ''')
        from llm.llm import LargeLanguageModel, LoopEngine
        for boundary in ('prune', 'remove1', 'remove2', 'marker', 'committed'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = await asyncio.to_thread(subprocess.run,
                    [sys.executable, '-c', code, directory, boundary],
                    timeout=30, capture_output=True, text=True)
                self.assertEqual(child.returncode, 77, child.stdout + child.stderr)
                self.assertEqual((root / 'reached').read_text(), boundary)
                context = json.loads((root / 'context.json').read_text())
                def forbidden(**request):
                    with (root / 'effects').open('a') as stream:
                        stream.write('unexpected replay\n')
                    raise AssertionError('history recovery cannot invoke a model')
                async with LargeLanguageModel(root / 'workspace', engines={'custom': LoopEngine(completion_fn=forbidden)}) as app:
                    project = await app.projects.aload(context['project_id'])
                    session = await project.sessions.aload(context['session_id'])
                    after = self.originals(session)
                    committed = boundary in ('marker', 'committed')
                    if not committed:
                        self.assertEqual(after, context['before'])
                    else:
                        prefix = 'runs/' + context['run_ids'][-1] + '/'
                        self.assertEqual({key: value for key, value in after.items() if key.startswith(prefix)},
                                         {key: value for key, value in context['before'].items() if key.startswith(prefix)})
                        self.assertFalse((session.paths.state / 'maintenance/retention.json').exists())
                        receipts = list((session.paths.state / 'retention_history').glob('*.json'))
                        self.assertEqual(len(receipts), 1)
                        self.assertEqual({row['run_id'] for row in json.loads(receipts[0].read_text())['runs']},
                                         set(context['run_ids'][:2]))
                    self.assertEqual({run.id for run in await session.run.alist()},
                                     set(context['run_ids'][-1:] if committed else context['run_ids']))
                    self.assertEqual(len(await session.aconversation()), 2 if committed else 6)
                    self.assertEqual((await project.arecovery()).issues, [])
                    self.assertEqual((root / 'effects').read_text(), 'called\n' * 3)
