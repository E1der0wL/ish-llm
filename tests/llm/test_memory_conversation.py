"""메모리 대화의 상태 계약, 실행 공유, 수명과 파일 저장소 격리를 검증한다."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus, RunStatus
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.services.configuration import ServiceConfig
from llm.services.history.conversation import ConversationStore, MemoryConversations, MemoryConversationStore
from llm.services.query import Query
from llm.services.infrastructure.storage import atomic_json, read_json
from tests.llm.test_facade_requests import ControlledEngine
from tests.llm.test_loop import ScriptedCompletion, chunk


class MemoryStoreTests(unittest.TestCase):
    def test_same_message_contract_without_files_or_shared_mutable_snapshots(self):
        store = MemoryConversationStore()
        metadata = {"nested": [1]}
        with patch.object(Path, "open", side_effect=AssertionError("Memory store attempted file I/O")):
            user = store.create(MessageRole.USER, "question", MessageStatus.QUEUED, metadata=metadata)
            metadata["nested"].append(2)
            user.metadata["nested"].append(3)
            self.assertEqual(store.get(user.id).metadata, {"nested": [1]})
            store.bind_run(user.id, "run")
            store.set_status(user.id, MessageStatus.COMMITTED)
            assistant = store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING, run_id="run")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: store.delta(assistant.id, "한"), range(40)))
            store.set_status(assistant.id, MessageStatus.COMPLETED)
            snapshot = store.list(query=Query(after=user.id, status="completed", limit=1))
            self.assertEqual(snapshot[0].content, "한" * 40)
            snapshot[0].content = "changed"
            self.assertEqual(store.get(assistant.id).content, "한" * 40)
            with self.assertRaises(ValueError):
                store.delta(assistant.id, "late")
            with self.assertRaises(ValueError):
                store.create(MessageRole.USER, "duplicate", MessageStatus.QUEUED, message_id=user.id)
            with self.assertRaises(ValueError):
                store.update_metadata(user.id, {"bad": float("nan")})
            self.assertNotIn("bad", store.get(user.id).metadata)
            store.clear()
            self.assertEqual(store.list(), [])


class MemoryConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def backend(self, **kwargs):
        app = LargeLanguageModel(self.root, engines={"test": ControlledEngine()}, **kwargs)
        self.addAsyncCleanup(app.shutdown)
        return app

    async def session(self, app):
        project = await app.projects.acreate("memory")
        return project, await project.sessions.acreate("conversation")

    async def test_configuration_is_explicit_validated_and_does_not_mutate_services(self):
        services = ServiceConfig()
        app = self.backend(services=services, conversation_storage="memory")
        self.assertIsNot(app.services, services)
        self.assertEqual(app.project_manager.sessions.conversations.default_storage, "memory")
        for mode in ("invalid", False, 7):
            with self.assertRaises(ValueError):
                self.backend(conversation_storage=mode)
        with self.assertRaises(ValueError):
            self.backend(services=ServiceConfig(conversations="memory"), conversation_storage="file")
        with self.assertRaises(TypeError):
            self.backend(services=ServiceConfig(conversations=None))
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_loop_and_facade_share_history_across_runtime_shutdown(self):
        completion = ScriptedCompletion([chunk("first answer", finish="stop")],
                                        [chunk("second answer", finish="stop")])
        app = LargeLanguageModel(self.root, conversation_storage="memory",
                                engines={"loop": LoopEngine(completion_fn=completion,
                                                             completion_kwargs={"model": "test"})})
        self.addAsyncCleanup(app.shutdown)
        project, session = await self.session(app)
        first = await session.run.submit("first", engine="loop")
        run = await first.wait(timeout=10)
        self.assertEqual((await run.aresponse()).content, "first answer")
        await session.run.shutdown()
        loaded = await project.sessions.aload(session.id)
        second = await loaded.run.submit("second", engine="loop")
        self.assertEqual((await (await second.wait(timeout=10)).aresponse()).content, "second answer")
        self.assertEqual([item["content"] for item in completion.requests[1]["messages"]],
                         ["first", "first answer", "second"])
        self.assertEqual(len(await session.aconversation()), 4)
        self.assertEqual((await (await loaded.run.arequest(first.id)).wait()).id, run.id)
        self.assertFalse(session.paths.conversation.exists())
        self.assertTrue((run.data.paths.root / "run.json").exists())
        self.assertEqual(len(await run.steps.alist()), 1)

    async def test_stream_queue_interrupt_and_other_sessions_are_isolated(self):
        app = self.backend(conversation_storage="memory")
        project, session = await self.session(app)
        engine = app.engines.resolve("test")
        engine.gates["held"] = asyncio.Event()
        streaming = asyncio.Event()

        def observe(run, event):
            if event.type == "text_delta" and event.delta.text == "answer:held":
                streaming.set()
        app.on_event = observe
        first = await session.run.submit("held", engine="test")
        await asyncio.wait_for(streaming.wait(), 10)
        queued = await session.run.submit("next", engine="test")
        self.assertEqual((await queued.aget_data()).status, MessageStatus.QUEUED)
        self.assertEqual((await session.aconversation())[1].content, "answer:held")
        other = await project.sessions.acreate("separate")
        independent = await (await other.run.submit("independent", engine="test")).wait(timeout=10)
        self.assertEqual((await independent.aresponse()).content, "answer:independent")
        await session.run.interrupt()
        interrupted = await first.wait(timeout=10)
        self.assertEqual(interrupted.data.status, RunStatus.INTERRUPTED)
        self.assertEqual((await interrupted.aresponse()).status, MessageStatus.INTERRUPTED)
        self.assertEqual((await (await queued.wait(timeout=10)).aresponse()).content, "answer:next")
        self.assertEqual(len(await other.aconversation()), 2)
        self.assertFalse(list(self.root.rglob("conversation.jsonl")))

    async def test_shutdown_drops_pending_memory_queue_and_new_backend_starts_empty(self):
        services = ServiceConfig(conversations="memory")
        app = self.backend(services=services)
        project, session = await self.session(app)
        engine = app.engines.resolve("test")
        engine.gates["held"] = asyncio.Event()
        await session.run.submit("held", engine="test")
        await asyncio.wait_for(engine.entered["held"].wait(), 10)
        await session.run.submit("never replay", engine="test")
        store = app.project_manager.sessions.conversations(session.data)
        await app.shutdown()
        self.assertEqual(store.list(), [])
        reopened = self.backend(services=services)
        restored = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
        self.assertEqual(await restored.aconversation(), [])
        await restored.run.start()
        await restored.run.wait_idle()
        self.assertEqual(len(await restored.run.alist()), 1)
        old_run = (await restored.run.alist())[0]
        self.assertEqual(old_run.data.status, RunStatus.INTERRUPTED)
        with self.assertRaises(KeyError):
            await old_run.aresponse()
        new = await (await restored.run.submit("fresh", engine="test")).wait(timeout=10)
        self.assertEqual((await new.aresponse()).content, "answer:fresh")

    async def test_clone_soft_delete_restore_and_permanent_cleanup(self):
        app = self.backend(conversation_storage="memory")
        project, session = await self.session(app)
        await (await session.run.submit("source", engine="test")).wait(timeout=10)
        await session.run.shutdown()
        cloned = await session.aclone()
        self.assertEqual([m.content for m in await cloned.aconversation()], ["source", "answer:source"])
        self.assertTrue(all(m.run_id is None for m in await cloned.aconversation()))
        self.assertNotEqual([m.id for m in await session.aconversation()], [m.id for m in await cloned.aconversation()])
        source_store = app.project_manager.sessions.conversations(session.data)
        await session.adelete()
        await session.arestore()
        self.assertEqual(len(await session.aconversation()), 2)
        other_project = await project.aclone()
        other_sessions = await other_project.sessions.alist()
        self.assertEqual(len(other_sessions), 2)
        other_store = app.project_manager.sessions.conversations(other_sessions[0].data)
        await session.adelete(permanent=True)
        self.assertEqual(source_store.list(), [])
        self.assertEqual(len(await cloned.aconversation()), 2)
        await other_project.adelete(permanent=True)
        self.assertEqual(other_store.list(), [])
        self.assertFalse(list(self.root.rglob("conversation.jsonl")))

    async def test_memory_does_not_import_modify_or_replace_existing_file_history(self):
        memory_app = self.backend(conversation_storage="memory")
        project, session = await self.session(memory_app)
        # 사용자가 별도로 남긴 파일이 있어도 명시적인 memory 선택을 바꾸지 않는다.
        file_store = ConversationStore(session.paths.conversation)
        with memory_app.project_manager.ownership.scope():
            file_store.create(MessageRole.USER, "file history", MessageStatus.COMMITTED)
            file_store.create(MessageRole.ASSISTANT, "answer:file history", MessageStatus.COMPLETED)
        original = session.paths.conversation.read_bytes()
        self.assertEqual(await session.aconversation(), [])
        await (await session.run.submit("memory history", engine="test")).wait(timeout=10)
        self.assertEqual([m.content for m in await session.aconversation()], ["memory history", "answer:memory history"])
        self.assertEqual(session.paths.conversation.read_bytes(), original)
        await memory_app.shutdown()
        file_again = self.backend(conversation_storage="file")
        reopened = await file_again.projects.aload(project.id)
        self.assertEqual(reopened.data.conversation_storage, "memory")
        loaded = await reopened.sessions.aload(session.id)
        self.assertEqual(await loaded.aconversation(), [])
        self.assertEqual(session.paths.conversation.read_bytes(), original)
        self.assertEqual([m.content for m in file_store.list()], ["file history", "answer:file history"])

    async def test_custom_memory_factory_lifetime_is_owned_by_caller(self):
        factory = MemoryConversations()
        services = ServiceConfig(conversations=factory)
        app = self.backend(services=services)
        project, session = await self.session(app)
        await (await session.run.submit("retained", engine="test")).wait(timeout=10)
        store = factory(session.data)
        await app.shutdown()
        self.assertEqual(len(store.list()), 2)
        reopened = self.backend(services=services)
        loaded = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
        self.assertEqual(len(await loaded.aconversation()), 2)
        await reopened.shutdown()
        factory.clear()
        self.assertEqual(store.list(), [])

    async def test_stale_runs_and_steps_recover_even_when_memory_history_is_gone(self):
        app = self.backend(conversation_storage="memory")
        project, session = await self.session(app)
        run = await (await session.run.submit("old", engine="test")).wait(timeout=10)
        run_path = run.data.paths.root / "run.json"
        step_path = (await run.steps.alist())[0].paths.root / "step.json"
        session_path = session.paths.root / "session.json"
        await app.shutdown()
        for path in (run_path, step_path):
            data = read_json(path)
            data.update(status="running", ended_at=None)
            atomic_json(path, data)
        session_data = read_json(session_path)
        session_data.update(status="running", current_run_id=run.id)
        atomic_json(session_path, session_data)
        reopened = self.backend(conversation_storage="memory")
        loaded = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
        await loaded.run.start()
        self.assertEqual(await loaded.aconversation(), [])
        self.assertEqual(read_json(run_path)["status"], "interrupted")
        self.assertEqual(read_json(step_path)["status"], "interrupted")
        self.assertEqual(read_json(session_path)["status"], "idle")
        self.assertEqual(len(await loaded.run.alist()), 1)

    async def test_factory_shares_one_store_across_threads_and_distinguishes_workspace_paths(self):
        app = self.backend(conversation_storage="memory")
        _, session = await self.session(app)
        factory = MemoryConversations()
        model = session.data
        with ThreadPoolExecutor(max_workers=4) as pool:
            stores = list(pool.map(lambda _: factory(deepcopy(model)), range(32)))
        self.assertTrue(all(store is stores[0] for store in stores))
        other = deepcopy(model)
        other.paths = type(model.paths)(self.root / "another-workspace" / model.id)
        self.assertIsNot(factory(other), stores[0])
