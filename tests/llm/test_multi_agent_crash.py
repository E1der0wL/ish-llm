"""Dedicated process death around D transaction/effect boundaries; fresh owners."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel, BackendServices
from llm.engines.base import BaseEngine, EngineEventType
from llm.engines.loop import LoopEngine
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.runtime.tools import ToolRuntime
from llm.services.runtime.operations import ToolOperations
from llm.services.runtime.runs import RunManager
from llm.services.history.conversation import ConversationStore
from tests.llm.support.fake_engine import FakeStreamingEngine
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call


def marker(root, **value):
    path = root.parent / 'marker.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**previous, **value}))


def die(root, point, **values):
    marker(root, reached=point, **values)
    os._exit(91)


async def fanout_child(root, point):
    entered = {n: asyncio.Event() for n in ('a', 'b')}
    never = asyncio.Event()
    class Probe(BaseEngine):
        async def run(self, context):
            entered[context.run.agent_id].set()
            await asyncio.gather(*(event.wait() for event in entered.values()))
            if point == 'one_finished' and context.run.agent_id == 'b':
                await never.wait()
            yield context.run.agent_id
            if point == 'stream':
                await never.wait()
    async def event(run, value):
        if point == 'stream' and value.type == EngineEventType.TEXT_DELTA:
            die(root, point, run_id=run.id)
    def run_event(value):
        if point == 'one_finished' and value.run.agent_id == 'a' and value.run.status == 'completed':
            die(root, point, run_id=value.run.id)
    async with LargeLanguageModel(root, engines={'probe': Probe()}, components=[], on_event=event, on_run_event=run_event) as app:
        project = await app.projects.acreate()
        session = await project.sessions.acreate()
        for name in entered:
            await project.agents.acreate({'engine': 'probe'}, identifier=name)
            await session.agents.aattach(name)
        marker(root, project=project.id, session=session.id)
        manager = app._manager(session._snapshot)
        create, put, new_run, io = ConversationStore.create, asyncio.Queue.put_nowait, RunManager._new_run, manager._io.run
        def creating(store, role, content, *args, **kwargs):
            if role == 'user' and content == 'fanout' and point == 'before_user':
                die(root, point)
            result = create(store, role, content, *args, **kwargs)
            if role == 'user' and content == 'fanout':
                marker(root, message=result.id)
                if point == 'written_user':
                    die(root, point)
            return result
        def queued(queue, value):
            if point == 'accepted' and manager._active is not None and queue is manager._active.queue and value is not None:
                die(root, point, message=value)
            return put(queue, value)
        def partial(self, runtime, message):
            result = new_run(self, runtime, message)
            if point == 'partial_group':
                die(root, point, partial_run=result.id)
            return result
        async def after_io(function, *args, **kwargs):
            result = await io(function, *args, **kwargs)
            if (point, getattr(function, '__name__', '')) in (
                    ('group', '_admit_group'), ('start', '_start_run'), ('binding', '_execution_context')):
                die(root, point)
            return result
        with patch.object(ConversationStore, 'create', creating), patch.object(asyncio.Queue, 'put_nowait', queued), patch.object(RunManager, '_new_run', partial), patch.object(manager._io, 'run', after_io):
            request = await session.run.submit('fanout', recipients='all')
            await request.wait_all(timeout=20)
        raise AssertionError('Unreached crash boundary: ' + point)


def tool_backend(root, point, *, recovery=False):
    entered = asyncio.Event()
    async def effect(arguments):
        with (root.parent / 'effects.jsonl').open('a') as stream:
            stream.write(json.dumps(arguments) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if point == 'effect' and not recovery:
            die(root, point)
        return 'saved'
    tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, effect,
        contract=ToolContract(approval_required=True)),)))
    model = ScriptedCompletion(*([[chunk('done', finish='stop')]] if recovery else [
        [chunk(calls=[call('{}', name='write')], finish='tool_calls')], [chunk('done', finish='stop')]]))
    class Hold(BaseEngine):
        async def run(self, context):
            entered.set()
            await asyncio.Event().wait()
            yield 'unreachable'
    app = LargeLanguageModel(root, engines={'loop': LoopEngine(completion_fn=model), 'hold': Hold()},
        components=[tools], services=BackendServices(tool_runtime=ToolRuntime(operation_key=lambda call: 'persisted-effect')))
    return app, model, entered


async def tool_child(root, point):
    app, _, entered = tool_backend(root, point)
    async with app:
        project = await app.projects.acreate(components=['tools'])
        await project.components.tools.aenable('write')
        session = await project.sessions.acreate()
        for name, definition in {'writer': {'engine': 'loop', 'completion': {'model': 'test/model'}, 'tools': ['write']}, 'other': {'engine': 'hold'}}.items():
            await project.agents.acreate(definition, identifier=name)
            await session.agents.aattach(name)
        marker(root, project=project.id, session=session.id)
        holding = await session.run.submit('hold', agent_id='other')
        await asyncio.wait_for(entered.wait(), 10)
        paused = await (await session.run.submit('write', agent_id='writer')).wait(timeout=10)
        assert paused.data.status == 'paused', paused.data.error
        marker(root, source=paused.id, other=holding.run.id)
        if point == 'approval':
            die(root, point)
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond('approve'))
        if point == 'approved':
            die(root, point)
        claim, complete = ToolOperations.claim, ToolOperations.complete
        async def claimed(self, call):
            value = await claim(self, call)
            if point == 'claim':
                die(root, point)
            return value
        async def completed(self, call, result):
            await complete(self, call, result)
            if point == 'receipt':
                die(root, point)
        with patch.object(ToolOperations, 'claim', claimed), patch.object(ToolOperations, 'complete', completed):
            request = await session.run.resume(paused.id)
            marker(root, resumed_request=request.id)
            resumed = await request.wait(timeout=10)
            assert resumed.data.status == 'completed', resumed.data.error
        die(root, point, completed=resumed.id)


class MultiAgentCrashTests(unittest.IsolatedAsyncioTestCase):
    async def child(self, root, point, kind):
        code = 'import asyncio,sys; from pathlib import Path; from tests.llm.test_multi_agent_crash import ' + kind + '; asyncio.run(' + kind + '(Path(sys.argv[1]),sys.argv[2]))'
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(root), point,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), 40)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        self.assertEqual(process.returncode, 91, out.decode() + err.decode())
        value = json.loads((root.parent / 'marker.json').read_text())
        self.assertEqual(value['reached'], point)
        return value

    async def test_f01_f06_f12_atomic_fanout_and_no_automatic_replay(self):
        for point in ('before_user', 'written_user', 'accepted', 'partial_group', 'group', 'start', 'binding', 'stream', 'one_finished'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'
                value = await self.child(root, point, 'fanout_child')
                probe = FakeStreamingEngine(chunks=('fresh execution',))
                async with LargeLanguageModel(root, engines={'probe': probe}, components=[]) as app:
                    project = await app.projects.aload(value['project'])
                    session = await project.sessions.aload(value['session'])
                    before = await session.run.alist()
                    original = {run.id: run.data.status for run in before}
                    messages = await session.aconversation()
                    if point in ('before_user', 'written_user'):
                        self.assertEqual(messages, [])
                        expected = 0
                    elif point in ('accepted', 'partial_group'):
                        self.assertEqual(len(messages), 1)
                        self.assertEqual(before, [])
                        expected = 2
                    else:
                        self.assertEqual(len(before), 2)
                        self.assertEqual(len(messages), 3)
                        expected = sum(state == 'pending' for state in original.values())
                    await session.run.start()
                    await asyncio.wait_for(session.run.wait_idle(), 10)
                    after = await session.run.alist()
                    self.assertEqual(len(probe.contexts), expected, (point, original))
                    if original:
                        self.assertEqual({r.id for r in after}, set(original))
                        for run in after:
                            self.assertEqual(run.data.status, 'completed' if original[run.id] in ('pending', 'completed') else 'interrupted')
                    self.assertFalse((await session.aget_data()).active_run_ids)
                    for run in after:
                        self.assertEqual(run.data.input_message_id, value['message'])
                        self.assertEqual((await run.aresponse()).run_id, run.id)
                    ids = {r.id for r in after}
                # A second independent Backend must not invoke either recipient.
                probe = FakeStreamingEngine()
                async with LargeLanguageModel(root, engines={'probe': probe}, components=[]) as app:
                    session = await (await app.projects.aload(value['project'])).sessions.aload(value['session'])
                    await session.run.start()
                    await asyncio.wait_for(session.run.wait_idle(), 10)
                    self.assertEqual(probe.contexts, [])
                    self.assertEqual({r.id for r in await session.run.alist()}, ids)
                print('D_FANOUT_CRASH=' + json.dumps({'point': point, 'ids': value, 'original': original, 'new_invocations': expected}))

    async def test_f07_f10_parallel_approval_claim_effect_receipt_survive_process_death(self):
        for point in ('approval', 'approved', 'claim', 'effect', 'receipt', 'completed'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'
                value = await self.child(root, point, 'tool_child')
                effect_path = root.parent / 'effects.jsonl'
                effects_before = effect_path.read_text().splitlines() if effect_path.exists() else []
                self.assertEqual(len(effects_before), int(point in ('effect', 'receipt', 'completed')))
                app, model, _ = tool_backend(root, point, recovery=True)
                async with app:
                    session = await (await app.projects.aload(value['project'])).sessions.aload(value['session'])
                    await session.run.start()
                    await asyncio.wait_for(session.run.wait_idle(), 10)
                    self.assertEqual(model.requests, [])
                    self.assertFalse((await session.aget_data()).active_run_ids)
                    other = await session.run.aload(value['other'])
                    self.assertEqual(other.data.status, 'interrupted')
                    source = await session.run.aload(value['source'])
                    self.assertEqual(source.data.status, 'paused')
                    if point in ('approval', 'approved'):
                        interaction, = await source.ainteractions()
                        if point == 'approval':
                            self.assertNotEqual(interaction.status, 'approved')
                            await source.arespond(interaction.respond('approve'))
                        result = await (await session.run.resume(source.id)).wait(timeout=10)
                        self.assertEqual(result.data.status, 'completed', result.data.error)
                        self.assertEqual(len(effect_path.read_text().splitlines()), 1)
                    else:
                        receipt = session.run.operation('persisted-effect')
                        self.assertEqual(receipt['status'], 'completed' if point in ('receipt', 'completed') else 'started')
                        self.assertEqual(effect_path.read_text().splitlines() if effect_path.exists() else [], effects_before)
                        steps = await (await session.run.aload(receipt['run_id'])).steps.alist()
                        self.assertTrue(any(step.id == receipt['step_id'] and step.metadata['operation_receipt']['status'] == receipt['status'] for step in steps))
                        if point in ('claim', 'effect'):
                            with self.assertRaises(Exception):
                                await session.run.resume(receipt['run_id'])
                            self.assertEqual(model.requests, [])
                            plan = await session.run.resume_plan(receipt['run_id'])
                            self.assertTrue(plan.retry_nodes)
                            retry = await (await session.run.resume(receipt['run_id'], retry_nodes=plan.retry_nodes)).wait(timeout=10)
                            self.assertEqual(retry.data.status, 'paused')
                            self.assertEqual(effect_path.read_text().splitlines() if effect_path.exists() else [], effects_before)
                            approval, = await retry.ainteractions(pending_only=True)
                            await retry.arespond(approval.respond('approve'))
                            rejected = await (await session.run.resume(retry.id)).wait(timeout=10)
                            self.assertEqual(rejected.data.status, 'failed')
                            self.assertEqual(model.requests, [])
                            self.assertEqual(effect_path.read_text().splitlines() if effect_path.exists() else [], effects_before)
                        elif point == 'receipt':
                            plan = await session.run.resume_plan(receipt['run_id'])
                            self.assertTrue(plan.retry_nodes)
                            resumed = await (await session.run.resume(receipt['run_id'], retry_nodes=plan.retry_nodes)).wait(timeout=10)
                            self.assertEqual(resumed.data.status, 'paused')
                            self.assertEqual(effect_path.read_text().splitlines(), effects_before)
                            approval, = await resumed.ainteractions(pending_only=True)
                            await resumed.arespond(approval.respond('approve'))
                            finished = await (await session.run.resume(resumed.id)).wait(timeout=10)
                            self.assertEqual(finished.data.status, 'completed', finished.data.error)
                            self.assertEqual(effect_path.read_text().splitlines(), effects_before)
                print('D_TOOL_CRASH=' + json.dumps({'point': point, 'ids': value, 'effects_before': len(effects_before)}))

    async def test_f11_damaged_checkpoint_after_parallel_crash_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            value = await self.child(root, 'approval', 'tool_child')
            app, model, _ = tool_backend(root, 'approval', recovery=True)
            async with app:
                session = await (await app.projects.aload(value['project'])).sessions.aload(value['session'])
                await session.run.start()
                await asyncio.wait_for(session.run.wait_idle(), 10)
                source = await session.run.aload(value['source'])
                files = list(source.data.paths.state.rglob('checkpoint.json'))
                self.assertTrue(files)
                original = files[0].read_bytes()
                files[0].write_bytes(b'{"schema_version":')
                with self.assertRaises((ValueError, RuntimeError)):
                    await session.run.resume(source.id)
                self.assertEqual(files[0].read_bytes(), b'{"schema_version":')
                self.assertEqual(model.requests, [])
                self.assertFalse((root.parent / 'effects.jsonl').exists())
                files[0].write_bytes(original)
                interaction, = await source.ainteractions(pending_only=True)
                await source.arespond(interaction.respond('approve'))
                resumed = await (await session.run.resume(source.id)).wait(timeout=10)
                self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                self.assertEqual(len((root.parent / 'effects.jsonl').read_text().splitlines()), 1)


    async def test_missing_active_index_cannot_authorize_delete_clone_or_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            value = await self.child(root, 'stream', 'fanout_child')
            session_root = root / 'projects' / value['project'] / 'sessions' / value['session']
            path = session_root / 'session.json'
            damaged = json.loads(path.read_text())
            damaged.update(status='idle', current_run_id=None, current_run_ids=[])
            path.write_text(json.dumps(damaged))
            originals = {p: p.read_bytes() for p in session_root.rglob('run.json')}
            probe = FakeStreamingEngine()
            async with LargeLanguageModel(root, engines={'probe': probe}, components=[]) as app:
                project = await app.projects.aload(value['project'])
                session = await project.sessions.aload(value['session'])
                for operation in (lambda: session.adelete(permanent=True), session.aclone,
                                  lambda: project.abackup(root.parent / 'backup')):
                    with self.assertRaisesRegex(ValueError, 'Run records'):
                        await operation()
                    self.assertEqual({p: p.read_bytes() for p in originals}, originals)
                await session.run.start()
                await asyncio.wait_for(session.run.wait_idle(), 10)
                self.assertEqual(probe.contexts, [])
                self.assertEqual([r.data.status for r in await session.run.alist()], ['interrupted', 'interrupted'])
                await session.run.shutdown()
                await session.adelete(permanent=True)
                self.assertFalse(session.paths.root.exists())
