"""Session 공개 계약, 재개방 및 이전 저장 형식의 비파괴 거부를 검증한다."""

import importlib
from pathlib import Path
import tempfile
import unittest

from llm.core import models
from llm.core.models import DOMAIN_STORAGE_VERSION, ProjectConfig, Session, SessionStatus
from llm.core.paths import SessionPaths
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.storage import atomic_json, read_json, read_domain_record, atomic_domain_json
from llm.services.lifecycle.sessions import SessionManager, SessionRepository


class SessionDomainTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_chain_storage_context_events_and_reopen(self):
        seen, events = [], []
        async def respond(context):
            seen.append(context)
            yield 'Session reply'

        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={'probe': BaseEngine(action=respond)},
                                          on_run_event=events.append) as app:
                config = ProjectConfig(session_defaults={'data': {'language': 'ko'}})
                project = await app.projects.acreate('Project', config=config, components=[])
                session = await project.sessions.acreate('Conversation')
                self.assertIsInstance(await session.aget_data(), Session)
                self.assertIsInstance(session.paths, SessionPaths)
                self.assertEqual(session.paths.root, project.paths.root / 'sessions' / session.id)
                self.assertEqual((await session.aget_data()).config['data']['language'], 'ko')
                request = await session.run.submit('Hello', engine='probe')
                run = await request.wait()
                self.assertEqual((await run.aresponse()).content, 'Session reply')
                self.assertEqual(seen[0].session.id, session.id)
                self.assertEqual(seen[0].run.session_id, session.id)
                self.assertFalse(hasattr(seen[0], 'task'))
                self.assertTrue(events)
                self.assertTrue(all(event.run.session_id == session.id for event in events))
                self.assertEqual((await session.run.astatus()).session_id, session.id)
                result = await run.aresult()
                self.assertEqual(result.session_id, session.id)
                steps = await run.steps.alist()
                records = [project.paths.root / 'project.json', session.paths.root / 'session.json',
                           (await run.aget_data()).paths.root / 'run.json', steps[0].paths.root / 'step.json']
                for path in records:
                    self.assertEqual(read_json(path)['storage_version'], DOMAIN_STORAGE_VERSION)
                self.assertNotIn('task_id', read_json(records[2]))
                self.assertFalse((project.paths.root / 'tasks').exists())
                self.assertFalse((session.paths.root / 'task.json').exists())
                self.assertEqual(DOMAIN_STORAGE_VERSION, 1)
                self.assertNotIn('tasks', vars(project))
                schema = app.project_schema()['properties']['config']['properties']
                self.assertIn('session_defaults', schema)
                self.assertNotIn('task_defaults', schema)
                ids = (project.id, session.id, run.id)

            async with LargeLanguageModel(directory, components=[], engines={}) as app:
                project = await app.projects.aload(ids[0])
                session = await project.sessions.aload(ids[1])
                self.assertEqual((await session.aget_data()).status, SessionStatus.IDLE)
                run = await session.run.aload(ids[2])
                self.assertEqual((await run.aresponse()).content, 'Session reply')
                self.assertEqual(len(await session.aconversation()), 2)

    async def test_unsupported_project_is_rejected_without_creating_sessions_or_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={}) as app:
                project = await app.projects.acreate(components=[])
                path = project.paths.root / 'project.json'
                project.paths.sessions.rmdir()
                legacy = project.paths.root / 'tasks' / ('a' * 32)
                legacy.mkdir(parents=True)
                (legacy / 'task.json').write_text('{"storage_version": 1}', encoding='utf-8')
                data = read_json(path)
                data['storage_version'] = 2
                atomic_json(path, data)
                before = {p: p.read_bytes() for p in project.paths.root.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'storage_version'):
                    await app.projects.aload(project.id)
                self.assertFalse(project.paths.sessions.exists())
                self.assertEqual(before, {p: p.read_bytes() for p in project.paths.root.rglob('*') if p.is_file()})

    async def test_all_record_boundaries_reject_version_two_and_stale_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('project.json', 'session.json', 'run.json', 'step.json'):
                with self.subTest(record=name):
                    path = Path(directory) / name
                    atomic_json(path, {'storage_version': 2, 'payload': 'preserve'})
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'storage_version'):
                        read_domain_record(path)
                    with self.assertRaisesRegex(ValueError, 'storage_version'):
                        atomic_domain_json(path, {'storage_version': DOMAIN_STORAGE_VERSION})
                    self.assertEqual(path.read_bytes(), before)

    async def test_new_domain_has_no_legacy_api_aliases(self):
        self.assertFalse(hasattr(models, 'Task'))
        self.assertFalse(hasattr(models, 'TaskStatus'))
        self.assertEqual(SessionManager.__module__, 'llm.services.lifecycle.sessions')
        self.assertEqual(SessionRepository.__module__, 'llm.services.lifecycle.sessions')
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module('llm.services.lifecycle.tasks')
        config = ProjectConfig(session_defaults={'data': {'setting': True}})
        self.assertEqual(config.for_engine('loop', session_config={'data': {'request': 1}})['data'], {'request': 1})
