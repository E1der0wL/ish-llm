"""Surviving Conversation references must not become zero Run usage after tree loss."""

import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from llm.core.models import SessionStatus

from tests.llm import test_usage_deletion as usage


class RunReferenceIntegrityTests(unittest.IsolatedAsyncioTestCase):
    create = usage.UsageDeletionTests.create
    run_once = usage.UsageDeletionTests.run_once

    async def missing_run(self):
        app, project, session, model = await self.create({'project_max_calls': 1})
        run = await self.run_once(session)
        self.assertEqual(run.data.status, 'completed')
        self.assertEqual(len(model.requests), 1)
        await session.run.shutdown()
        shutil.rmtree(run.data.paths.root)
        return app, project, session, model

    async def test_startup_cannot_clear_surviving_references_and_repeat_model_call(self):
        _, _, session, model = await self.missing_run()
        path = session.paths.conversation
        before = path.read_bytes()
        rejected = False
        try:
            await self.run_once(session)
        except (ValueError, OSError):
            rejected = True
        self.assertEqual({'rejected': rejected, 'model_calls': len(model.requests),
                          'source_preserved': path.read_bytes() == before},
                         {'rejected': True, 'model_calls': 1, 'source_preserved': True})

    async def test_other_session_cannot_treat_missing_referenced_run_as_zero_usage(self):
        _, project, session, model = await self.missing_run()
        before = session.paths.conversation.read_bytes()
        rejected = False
        try:
            await project.amodel_usage()
        except (ValueError, OSError):
            rejected = True
        other = await project.sessions.acreate()
        try:
            await self.run_once(other)
        except (ValueError, OSError):
            pass
        self.assertEqual({'usage_rejected': rejected, 'model_calls': len(model.requests),
                          'source_preserved': session.paths.conversation.read_bytes() == before},
                         {'usage_rejected': True, 'model_calls': 1, 'source_preserved': True})

    async def test_backup_preserves_source_and_destination_when_run_reference_is_lost(self):
        _, project, session, _ = await self.missing_run()
        before = session.paths.conversation.read_bytes()
        with TemporaryDirectory() as root:
            destination = Path(root) / 'backup'
            with self.assertRaisesRegex(ValueError, 'referenced Run'):
                await project.abackup(destination)
            self.assertFalse(destination.exists())
        self.assertEqual(session.paths.conversation.read_bytes(), before)

    async def test_memory_conversation_reopens_without_creating_a_file_or_replaying_calls(self):
        app, source, _, model = await self.create({'project_max_calls': 2})
        project = await app.projects.acreate(components=[], conversation_storage='memory',
                                            config=(await source.aget_data()).config)
        session = await project.sessions.acreate()
        self.assertEqual((await self.run_once(session)).data.status, 'completed')
        await session.run.shutdown()
        self.assertFalse(session.paths.conversation.exists())
        self.assertEqual((await self.run_once(session)).data.status, 'completed')
        self.assertEqual(len(model.requests), 2)
        self.assertFalse(session.paths.conversation.exists())

    async def test_current_run_pointer_is_required_even_without_a_conversation_file(self):
        app, source, _, _ = await self.create({})
        project = await app.projects.acreate(components=[], conversation_storage='memory',
                                            config=(await source.aget_data()).config)
        session = await project.sessions.acreate()
        data = await session.aget_data()
        data.status, data.current_run_id = SessionStatus.RUNNING, '0' * 32
        with app.project_manager.ownership.scope():
            app.project_manager.sessions.repository.save(data)
            path = data.paths.root / 'session.json'
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'referenced Run'):
                app.run_repository.list(data)
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(data.paths.conversation.exists())
