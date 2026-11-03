"""Surviving Conversation references must not become zero Run usage after tree loss."""

import shutil
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

from llm.core.models import SessionStatus

from tests.llm import test_usage_deletion as usage


class RunReferenceIntegrityTests(unittest.IsolatedAsyncioTestCase):
    create = usage.UsageDeletionTests.create
    run_once = usage.UsageDeletionTests.run_once

    async def test_memory_and_custom_store_references_block_missing_tree_and_quota_reset(self):
        from llm.llm import LargeLanguageModel, LoopEngine
        from llm.services.composition import BackendServices
        from llm.services.history.conversation import MemoryConversations
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from tests.llm.test_loop import ScriptedCompletion, chunk

        for custom in (False, True):
            with self.subTest(custom=custom), TemporaryDirectory() as root:
                model = ScriptedCompletion(*([[chunk('answer', finish='stop')]] * 2))
                services = BackendServices(conversations=MemoryConversations() if custom else 'memory')
                async with LargeLanguageModel(root, components=[], services=services,
                        engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}) as app:
                    project = await app.projects.acreate(components=[], config={
                        'policies': {'usage': {'project_max_calls': 1}}})
                    await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test'}}, identifier='loop')
                    session = await project.sessions.acreate(agent_ids=['loop'])
                    run = await self.run_once(session)
                    self.assertEqual(run.data.status, 'completed', run.data.error)
                    await session.run.shutdown()
                    self.assertEqual(len(await session.aconversation()), 2)
                    self.assertFalse(session.paths.conversation.exists())
                    # A stray unselected file must neither become evidence nor be imported.
                    session.paths.conversation.write_text('not the selected conversation')
                    self.assertEqual((await project.amodel_usage())['call_count'], 1)
                    self.assertTrue(run.data.paths.root.resolve().is_relative_to(Path(root).resolve()))
                    shutil.rmtree(run.data.paths.root)
                    with self.assertRaisesRegex(ValueError, 'referenced Run'):
                        await project.amodel_usage()
                    with self.assertRaisesRegex(ValueError, 'referenced Run'):
                        await session.run.alist()
                    other = await project.sessions.acreate(agent_ids=['loop'])
                    try:
                        await self.run_once(other)
                    except ValueError:
                        pass
                    self.assertEqual(len(model.requests), 1)
                    self.assertEqual(len(await session.aconversation()), 2)
                    self.assertEqual(session.paths.conversation.read_text(), 'not the selected conversation')

    def test_unbound_repository_does_not_guess_conversation_storage(self):
        from llm.services.runtime.runs import RunRepository
        with self.assertRaisesRegex(RuntimeError, 'selected Conversation'):
            list(RunRepository().record_paths(SimpleNamespace()))

    async def test_archive_uses_explicit_store_without_rebinding_live_factory(self):
        import asyncio
        app, project, session, _ = await self.create({})
        run = await self.run_once(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        await session.run.shutdown()
        with TemporaryDirectory() as directory:
            archive = await project.abackup(Path(directory) / 'archive')
            repository = app.project_manager.sessions.run_repository
            selected = repository.conversations
            with patch.object(repository, 'conversations', side_effect=AssertionError('live factory used for archive')):
                checked = await asyncio.to_thread(app.project_manager._validate_archive, archive / 'project')
                self.assertEqual(checked.paths.root, archive / 'project')
            self.assertIs(repository.conversations, selected)
            self.assertEqual([item.id for item in repository.list(session.data)], [run.id])

    async def test_selected_file_store_rejects_link_even_when_cached_inode_is_unchanged(self):
        _, project, session, _ = await self.create({})
        run = await self.run_once(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        await session.run.shutdown()
        self.assertEqual((await project.amodel_usage())['call_count'], 1)
        path = session.paths.conversation
        detached = path.with_name('detached.jsonl')
        path.rename(detached)
        path.symlink_to(detached)
        try:
            with self.assertRaises(ValueError):
                await project.amodel_usage()
            with self.assertRaises(ValueError):
                await session.aconversation()
        finally:
            path.unlink()
            detached.rename(path)
        self.assertEqual((await project.amodel_usage())['call_count'], 1)

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
        other = await project.sessions.acreate(agent_ids=["loop"])
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
        await project.agents.acreate(await source.agents.aload('loop'), identifier='loop')
        session = await project.sessions.acreate(agent_ids=["loop"])
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
        await project.agents.acreate(await source.agents.aload('loop'), identifier='loop')
        session = await project.sessions.acreate(agent_ids=["loop"])
        data = await session.aget_data()
        data.status, data.active_run_ids = SessionStatus.RUNNING, ['0' * 32]
        with app.project_manager.ownership.scope():
            app.project_manager.sessions.repository.save(data)
            path = data.paths.root / 'session.json'
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'referenced Run'):
                app.run_repository.list(data)
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(data.paths.conversation.exists())
