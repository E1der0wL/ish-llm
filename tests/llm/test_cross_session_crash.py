"""F13: real process death before/after one atomic source/target acceptance."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.engines.base import BaseEngine
from llm.services.history.conversation import ConversationStore
from llm.services.runtime.transfers import TransferAdmission
from llm.services.infrastructure.storage import atomic_json


def marker(root, **values):
    path = root.parent / 'marker.json'
    old = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**old, **values}))


class Effect(BaseEngine):
    async def run(self, context):
        path = context.project.paths.root.parent.parent.parent / 'effects.jsonl'
        with path.open('a') as stream:
            stream.write(json.dumps({'run': context.run.id, 'message': context.run.input_message_id}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        yield 'done'


async def transfer_child(root, point):
    def die(**values):
        marker(root, reached=point, **values)
        os._exit(93)
    async with LargeLanguageModel(root, engines={'effect': Effect()}, components=[]) as app:
        project = await app.projects.acreate()
        source, target = await project.sessions.acreate(), await project.sessions.acreate()
        await project.agents.acreate({'engine': 'effect'}, identifier='a')
        await target.agents.aattach('a')
        marker(root, project=project.id, source=source.id, target=target.id)
        create, queue = ConversationStore.create, asyncio.Queue.put_nowait
        def creating(store, role, content, *args, **kwargs):
            if role == 'user' and content == 'transfer' and point == 'before_user':
                die()
            result = create(store, role, content, *args, **kwargs)
            if role == 'user' and content == 'transfer':
                marker(root, message=result.id)
                if point == 'target_written':
                    die()
            return result
        def receipt(path, value):
            atomic_json(path, value)
            if path.parent.name == 'transfers' and point == 'receipt_written':
                die(transfer=value['id'])
        def queued(work, value):
            if value is not None and point == 'committed':
                die(message=value)
            return queue(work, value)
        with patch.object(ConversationStore, 'create', creating), \
                patch('llm.services.runtime.transfers.atomic_json', receipt), \
                patch.object(asyncio.Queue, 'put_nowait', queued):
            transfer = await source.exchange.send(target.id, 'transfer', agent_ids=('a',), key='durable')
            await transfer.wait(timeout=15)
        raise AssertionError('Unreached F13 boundary')


class CrossSessionCrash(unittest.IsolatedAsyncioTestCase):
    async def test_atomic_transport_process_death_and_same_key_reopen_never_duplicates(self):
        for point in ('before_user', 'target_written', 'receipt_written', 'committed'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'
                script = ('import asyncio; from pathlib import Path; '
                          'from tests.llm.test_cross_session_crash import transfer_child; '
                          f'asyncio.run(transfer_child(Path({str(root)!r}), {point!r}))')
                child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script],
                                                capture_output=True, text=True, timeout=30)
                self.assertEqual(child.returncode, 93, child.stdout + child.stderr)
                value = json.loads((root.parent / 'marker.json').read_text())
                self.assertEqual(value['reached'], point)
                self.assertFalse((root.parent / 'effects.jsonl').exists())
                async with LargeLanguageModel(root, engines={'effect': Effect()}, components=[]) as app:
                    project = await app.projects.aload(value['project'])
                    source = await project.sessions.aload(value['source'])
                    target = await project.sessions.aload(value['target'])
                    messages = await target.aconversation()
                    self.assertEqual(len(messages), 1 if point == 'committed' else 0)
                    self.assertEqual(await target.run.alist(), [])
                    if point != 'committed':
                        with self.assertRaises(FileNotFoundError):
                            await source.exchange.aget('durable')
                    transfer = await source.exchange.send(target.id, 'transfer', agent_ids=('a',), key='durable')
                    result = await transfer.wait(timeout=10)
                    self.assertEqual(result['status'], 'completed')
                    self.assertEqual(len(result['runs']), 1)
                    if point == 'committed':
                        self.assertEqual(result['message_id'], value['message'])
                    identifier = result['id']
                effects = (root.parent / 'effects.jsonl').read_text().splitlines()
                self.assertEqual(len(effects), 1)
                async with LargeLanguageModel(root, engines={'effect': Effect()}, components=[]) as app:
                    project = await app.projects.aload(value['project'])
                    source = await project.sessions.aload(value['source'])
                    transfer = await source.exchange.send(value['target'], 'transfer', agent_ids=('a',), key='durable')
                    same = await transfer.wait(timeout=10)
                    self.assertEqual(same['id'], identifier)
                    self.assertEqual(same, result)
                self.assertEqual((root.parent / 'effects.jsonl').read_text().splitlines(), effects)
                print('E_F13=' + json.dumps({'point': point, 'marker': value, 'result': result,
                                             'effects': [json.loads(row) for row in effects]}))

    async def test_caller_cancel_drains_acceptance_without_losing_correlation(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            async with LargeLanguageModel(root, engines={'effect': Effect()}, components=[]) as app:
                project = await app.projects.acreate()
                source, target = await project.sessions.acreate(), await project.sessions.acreate()
                await project.agents.acreate({'engine': 'effect'}, identifier='a')
                await target.agents.aattach('a')
                entered, release = threading.Event(), threading.Event()
                original = TransferAdmission.persist
                def persist(admission, message):
                    result = original(admission, message)
                    entered.set()
                    if not release.wait(10):
                        raise TimeoutError('test release missing')
                    return result
                with patch.object(TransferAdmission, 'persist', persist):
                    send = asyncio.create_task(source.exchange.send(target.id, 'transfer', agent_ids=('a',), key='cancel'))
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                        send.cancel()
                    finally:
                        release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await send
                transfer = await source.exchange.aget('cancel')
                result = await transfer.wait(timeout=10)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(len(result['runs']), 1)
                self.assertEqual(len((root.parent / 'effects.jsonl').read_text().splitlines()), 1)
