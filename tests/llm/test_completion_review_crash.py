"""F14: dedicated process death around review ACK and final publication."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from llm.components.processing import CompletionSession, CompletionReview, ReviewAction
from llm.components.tools import Tool, ToolRegistry
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, BackendServices
from llm.services.runtime.tools import ToolRuntime
from tests.llm.support.common_provider import CommonProvider
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_completion_processing import Feature
from tests.llm.test_completion_review import answer, reviewer


def append(root, name, value):
    with (root.parent / (name + '.jsonl')).open('a') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def rows(root, name):
    path = root.parent / (name + '.jsonl')
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def marker(root, **value):
    path = root.parent / 'marker.json'
    old = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**old, **value}))


def die(root, point, run_id):
    marker(root, reached=point, run=run_id)
    os._exit(94)


class RecordedProvider(CommonProvider):
    def __init__(self, root, *attempts):
        super().__init__(*attempts)
        self.root = root

    async def stream(self, request, **kwargs):
        append(self.root, 'models', {'ordinal': len(rows(self.root, 'models')) + 1})
        async for envelope in super().stream(request, **kwargs):
            yield envelope


def backend(root, point, *, recovery=False):
    class Review(CompletionSession):
        def __init__(self, context): self.run_id = context.run.id
        async def review_final(self, value):
            action = ReviewAction.CONTINUE if value.answer.message.text == 'candidate' else ReviewAction.ACCEPT
            append(root, 'reviews', {'run': self.run_id, 'iteration': value.iteration, 'action': str(action)})
            if point == 'computed' and not recovery and action == ReviewAction.CONTINUE:
                die(root, point, self.run_id)
            return CompletionReview(action, 'check the candidate')
    async def effect(arguments):
        append(root, 'effects', {'value': 'written'})
        return 'saved'
    tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, effect),)))
    feature = Feature('review_check', reviewer(Review))
    attempts = [answer('accepted')] if recovery else [
        [{'sealed': {'invoke': [{'identity': 'write-1', 'operation': 'write', 'input': {}}]}}],
        answer('candidate'), answer('accepted')]
    provider = RecordedProvider(root, *attempts)
    async def observe(run, event):
        if recovery:
            return
        key = event.metadata.get('key') if event.type == 'checkpoint' else None
        wanted = {'responded': 'iteration:2', 'attempt': 'review_attempt:2',
                  'continue': 'review:2', 'accept': 'review:3'}.get(point)
        if wanted is not None and key == wanted:
            die(root, point, run.id)
        if point == 'published' and event.type == 'output' and event.output.step_id is None:
            die(root, point, run.id)
    app = LargeLanguageModel(root, components=[tools, feature], engines={'loop': LoopEngine(completion_adapter=provider)},
        services=BackendServices(tool_runtime=ToolRuntime(operation_key=lambda call: 'f-review-effect')),
        on_event=observe)
    return app, provider


async def child(root, point):
    app, _ = backend(root, point)
    async with app:
        project = await app.projects.acreate(components=['tools', 'review_check'])
        await project.components.tools.aenable('write')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'fake'}, 'tools': ['write']}, identifier='writer')
        session = await project.sessions.acreate()
        await session.agents.aattach('writer')
        marker(root, project=project.id, session=session.id)
        result = await (await session.run.submit('original', agent_id='writer')).wait(timeout=15)
        raise AssertionError('Unreached F14 boundary: ' + point + ': ' + str(result.data.error))


class ReviewCrashTests(unittest.IsolatedAsyncioTestCase):
    async def test_f14_response_attempt_decision_and_publication_survive_process_death(self):
        for point in ('responded', 'attempt', 'computed', 'continue', 'accept', 'published'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'
                code = ('import asyncio,sys; from pathlib import Path; '
                        'from tests.llm.test_completion_review_crash import child; '
                        'asyncio.run(child(Path(sys.argv[1]),sys.argv[2]))')
                process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(root), point,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    out, err = await asyncio.wait_for(process.communicate(), 40)
                except BaseException:
                    process.kill()
                    await process.wait()
                    raise
                self.assertEqual(process.returncode, 94, out.decode() + err.decode())
                evidence = json.loads((root.parent / 'marker.json').read_text())
                self.assertEqual(evidence['reached'], point)
                self.assertEqual(rows(root, 'effects'), [{'value': 'written'}])
                before_models, before_reviews = len(rows(root, 'models')), len(rows(root, 'reviews'))
                app, provider = backend(root, point, recovery=True)
                async with app:
                    project = await app.projects.aload(evidence['project'])
                    session = await project.sessions.aload(evidence['session'])
                    await session.run.start()
                    await asyncio.wait_for(session.run.wait_idle(), 10)
                    source, = await session.run.alist()
                    self.assertEqual(source.id, evidence['run'])
                    self.assertEqual(source.data.status, 'interrupted')
                    old_response = await source.aresponse()
                    self.assertEqual(old_response.content, 'accepted' if point == 'published' else '')
                    self.assertEqual(len(provider.requests), 0)
                    self.assertEqual(len(rows(root, 'models')), before_models)
                    self.assertEqual(len(rows(root, 'reviews')), before_reviews)
                    self.assertEqual(len(rows(root, 'effects')), 1)
                    resumed = await (await session.run.resume(source.id)).wait(timeout=15)
                    self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                    self.assertNotEqual(source.id, resumed.id)
                    self.assertEqual(resumed.data.metadata['resume']['run_id'], source.id)
                    self.assertEqual((await resumed.aresponse()).content, 'accepted')
                    self.assertEqual(len(provider.requests), 0 if point in ('accept', 'published') else 1)
                    self.assertEqual(len(rows(root, 'models')), 3)
                    self.assertEqual(len(rows(root, 'reviews')), 3 if point == 'computed' else 2)
                    self.assertEqual(rows(root, 'effects'), [{'value': 'written'}])
                    checkpoint = await resumed.acheckpoint('loop')
                    self.assertEqual(checkpoint['records']['review:2']['action'], 'continue')
                    self.assertEqual(checkpoint['records']['review:3']['action'], 'accept')
                    self.assertEqual(checkpoint['records']['review_attempt:2']['attempt'],
                                     2 if point in ('attempt', 'computed') else 1)
                    reused = [s for s in await resumed.steps.alist() if s.kind == 'tool']
                    self.assertEqual(len(reused), 1)
                    self.assertTrue(reused[0].metadata['reused'])
                    self.assertFalse((await session.aget_data()).active_run_ids)
                    evidence.update(resumed=resumed.id, models=len(rows(root, 'models')),
                                    reviews=len(rows(root, 'reviews')), effects=len(rows(root, 'effects')))
                again, never = backend(root, point, recovery=True)
                async with again:
                    project = await again.projects.aload(evidence['project'])
                    session = await project.sessions.aload(evidence['session'])
                    await session.run.start()
                    await asyncio.wait_for(session.run.wait_idle(), 10)
                    records = await session.run.alist()
                    self.assertEqual({r.id: str(r.data.status) for r in records},
                                     {evidence['run']: 'interrupted', evidence['resumed']: 'completed'})
                    self.assertEqual(len(never.requests), 0)
                    self.assertEqual(len(rows(root, 'effects')), 1)
                print('F_F14=' + json.dumps({'point': point, **evidence}))
