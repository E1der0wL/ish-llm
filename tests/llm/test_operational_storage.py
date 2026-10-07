"""장시간 실행을 위한 호출 상한, 출력 저장 경계와 휴대 가능한 백업을 검증한다."""
from tests.llm.configuration_fixtures import rag_settings, rag_project

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from contextlib import aclosing
from llm.core.models import RunStatus
from llm.core.results import EngineDelta
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.llm import LargeLanguageModel
from llm.providers.calls import ProviderCalls, ProviderLimits, ProviderCapacityError
from llm.providers.litellm import stream_completion
from llm.services.composition import BackendServices
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.infrastructure.journal import OutputJournal
from llm.services.infrastructure.storage import atomic_json, read_json
from llm.services.runtime.output import OutputBuffer, consume_events
from llm.services.runtime.runs import RunRepository


class ProviderCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_sessions_share_injected_capacity_and_report_error_code(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        pool = ProviderCalls(ProviderLimits(max_active=1, max_waiting=0))
        def request(**kwargs):
            entered.set()
            release.wait(5)
            yield "done"
        async def work(context):
            async with aclosing(stream_completion({}, completion_fn=request)) as stream:
                async for text in stream:
                    yield text
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={"test": BaseEngine(action=work)},
                    services=BackendServices(provider_calls=pool)) as app:
                project = await app.projects.acreate()
                first, second = await project.sessions.acreate(), await project.sessions.acreate()
                original = await first.run.submit("first", engine="test")
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await first.run.interrupt()
                self.assertEqual((await original.wait()).data.status, RunStatus.INTERRUPTED)
                blocked = await (await second.run.submit("second", engine="test")).wait(timeout=5)
                self.assertEqual(blocked.data.error_code, "provider_capacity")
                self.assertEqual(pool.stats["active"], 1)
                release.set()
                for _ in range(200):
                    if not pool.stats["active"]:
                        break
                    await asyncio.sleep(0.01)
                completed = await (await second.run.submit("third", engine="test")).wait(timeout=5)
                self.assertEqual(completed.data.status, RunStatus.COMPLETED)

    async def test_cancel_retains_slot_until_blocked_call_and_close_finish(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = ProviderCalls(ProviderLimits(max_active=1, max_waiting=0))
        def request(**kwargs):
            entered.set()
            release.wait(5)
            try:
                yield "late"
            finally:
                closed.set()
        async def consume():
            with calls.scope():
                async with aclosing(stream_completion({}, completion_fn=request)) as stream:
                    async for item in stream:
                        self.fail("Cancelled request delivered output")
        worker_future = asyncio.create_task(consume())
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        worker_future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_future
        self.assertEqual(calls.stats, {"active": 1, "waiting": 0})
        with self.assertRaises(ProviderCapacityError):
            await calls.acquire()
        release.set()
        for _ in range(200):
            if calls.stats["active"] == 0:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(calls.stats["active"], 0)
        self.assertTrue(closed.is_set())
        await calls.acquire()
        calls.release()

    async def test_waiters_are_bounded_cancelled_and_expire(self):
        calls = ProviderCalls(ProviderLimits(max_active=1, max_waiting=1, wait_seconds=0.05))
        await calls.acquire()
        waiter = asyncio.create_task(calls.acquire())
        await asyncio.sleep(0)
        with self.assertRaises(ProviderCapacityError):
            await calls.acquire()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(calls.stats["waiting"], 0)
        with self.assertRaisesRegex(ProviderCapacityError, "timed out"):
            await calls.acquire()
        calls.release()

    async def test_start_failure_releases_slot(self):
        calls = ProviderCalls()
        with patch("llm.providers.litellm.threading.Thread.start", side_effect=RuntimeError("start")):
            with self.assertRaisesRegex(RuntimeError, "start"):
                async for _ in stream_completion({}, calls=calls):
                    pass
        self.assertEqual(calls.stats["active"], 0)


class IndexedJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "outputs.jsonl"
        self.journal = OutputJournal(index_stride=16)
        self.values = [EngineDelta("answer", str(i), sequence=i) for i in range(1, 1025)]
        self.journal.append(self.path, self.values)

    def test_cached_cursor_decodes_only_nearby_records(self):
        self.assertEqual(self.journal.read(self.path, after=1000, limit=3), self.values[1000:1003])
        with patch.object(self.journal, "_decode", wraps=self.journal._decode) as decode:
            self.assertEqual(self.journal.read(self.path, after=1000, limit=3), self.values[1000:1003])
        self.assertLessEqual(decode.call_count, 16 + 3)

    def test_corrupt_missing_and_stale_index_rebuilds_and_tail_is_ignored(self):
        self.journal.read(self.path, after=1000)
        cache = self.path.with_name("outputs.index.json")
        for corrupt in ("broken json", '{"index": {}, "checksum": "wrong"}'):
            cache.write_text(corrupt)
            self.assertEqual(self.journal.read(self.path, after=1023), self.values[-1:])
        cache.unlink()
        extra = EngineDelta("answer", "extra", sequence=1025)
        self.journal.append(self.path, [extra])
        self.assertEqual(self.journal.read(self.path, after=1024), [extra])
        with self.path.open("ab") as stream:
            stream.write(b'{"incomplete"')
        self.assertEqual(self.journal.read(self.path, after=1024), [extra])

    def test_replacement_and_bad_sequence_are_not_hidden_by_index(self):
        self.journal.read(self.path, after=1000)
        self.path.unlink()
        self.journal.append(self.path, [EngineDelta("answer", "new", sequence=1)])
        self.assertEqual(self.journal.read(self.path, after=1000), [])
        self.journal.append(self.path, [EngineDelta("answer", "bad", sequence=8)])
        with self.assertRaisesRegex(ValueError, "sequence"):
            self.journal.read(self.path, after=1)


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupt_does_not_wait_forever_for_async_observer(self):
        entered = asyncio.Event()
        async def observe(run, event):
            if event.type == EngineEventType.TEXT_DELTA:
                entered.set()
                await asyncio.Event().wait()
        async def work(context):
            yield "partial"
            yield "more"
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={"test": BaseEngine(action=work)}, on_event=observe,
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=2))) as app:
                session = await (await app.projects.acreate()).sessions.acreate()
                request = await session.run.submit("go", engine="test")
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(session.run.interrupt(), 3)
                run = await request.wait(timeout=3)
                self.assertEqual(run.data.status, RunStatus.INTERRUPTED)
                self.assertEqual((await run.aresponse()).content, "partialmore")

    async def test_cancellation_before_timer_still_flushes_buffer(self):
        saved, delivered = [], asyncio.Event()
        async def events():
            yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(text="accepted"))
            delivered.set()
            await asyncio.Event().wait()
        async def handle(batch):
            saved.extend(batch)
        worker_future = asyncio.create_task(consume_events(events(), OutputBuffer(batch_size=8, max_delay=60), handle))
        await delivered.wait()
        await asyncio.sleep(0.02)
        self.assertEqual(saved, [])
        worker_future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_future
        self.assertEqual([event.delta.text for event in saved], ["accepted"])

    async def test_generator_close_error_is_not_suppressed(self):
        class Events:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration
            async def aclose(self):
                raise RuntimeError("close failed")
        async def handle(batch):
            pass
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await asyncio.wait_for(consume_events(Events(), OutputBuffer(batch_size=2), handle), 2)

    async def test_graph_pause_backup_restore_and_resume_does_not_repeat_effects(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.graph import GraphEngine
        calls = []
        async def work(node):
            calls.append(node.node_id)
            return {"last": node.node_id}
        engine = GraphEngine(handlers={"work": work})
        graph = (WorkflowGraph(entry="a").node("a", "work").node("b", "work", pause_before=True)
                 .node("end", "end").connect("a", "b").connect("b", "end").to_dict())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            async with LargeLanguageModel(root / "source", engines={"graph": engine},
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=16))) as app:
                project = await app.projects.acreate(components=["workflows"])
                await project.components.workflows.acreate(graph, identifier="flow")
                session = await project.sessions.acreate()
                paused = await (await session.run.submit("work", engine="graph", engine_options={"workflow": "flow"})).wait(timeout=10)
                self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
                await session.run.shutdown()
                backup = await project.abackup(root / "backup")
            async with LargeLanguageModel(root / "restored", engines={"graph": engine},
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=16))) as app:
                restored = await app.projects.arestore_backup(backup)
                session = await restored.sessions.aload(session.id)
                resumed = await (await session.run.resume(paused.id, engine="graph")).wait(timeout=10)
                self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
                self.assertEqual(calls, ["a", "b"])

    async def test_barrier_is_persisted_before_effect_and_generator_keeps_same_task(self):
        owner, saved, effects, batches = [], [], [], []
        async def events():
            for i in range(10):
                owner.append(asyncio.current_task())
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(text=str(i)))
            yield EngineEvent(EngineEventType.STEP_UPDATED)
            self.assertEqual(len(saved), 11)
            effects.append("executed")
        async def handle(batch):
            batches.append(len(batch))
            saved.extend(batch)
        await consume_events(events(), OutputBuffer(batch_size=4), handle)
        self.assertEqual(batches, [4, 4, 2, 1])
        self.assertEqual(len(set(owner)), 1)
        self.assertEqual(effects, ["executed"])

    async def test_timer_and_cancel_flush_accepted_output(self):
        saved, entered = [], asyncio.Event()
        async def events():
            yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(text="partial"))
            entered.set()
            await asyncio.Event().wait()
        async def handle(batch):
            saved.extend(batch)
        worker_future = asyncio.create_task(consume_events(events(), OutputBuffer(batch_size=8, max_delay=0.01), handle))
        await entered.wait()
        for _ in range(100):
            if saved:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(saved[0].delta.text, "partial")
        worker_future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_future

    async def test_storage_failure_does_not_ack_tool_barrier(self):
        effects = []
        async def events():
            yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(text="text"))
            yield EngineEvent(EngineEventType.STEP_UPDATED)
            effects.append("unsafe")
        async def handle(batch):
            raise OSError("disk unavailable")
        with self.assertRaises(OSError):
            await consume_events(events(), OutputBuffer(batch_size=8), handle)
        self.assertEqual(effects, [])

    async def test_integrated_batches_preserve_sequence_replace_and_durable_notifications(self):
        async def action(context):
            for text in ("one", "two", "three"):
                yield text
            yield EngineDelta(text="final", operation="replace")
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={"test": BaseEngine(action=action)},
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=8))) as backend:
                project = await backend.projects.acreate()
                session = await project.sessions.acreate()
                errors = []
                def observe(run, event):
                    if event.delta is not None:
                        try:
                            self.assertEqual(backend.run_repository.output_events(run,
                                after=event.delta.sequence - 1, limit=1), [event.delta])
                        except Exception as error:
                            errors.append(error)
                backend.on_event = observe
                with patch.object(backend.run_repository.output_journal, "append",
                                  wraps=backend.run_repository.output_journal.append) as append:
                    run = await (await session.run.submit("go", engine="test")).wait(timeout=10)
                self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
                self.assertEqual((await run.aresponse()).content, "final")
                self.assertEqual(errors, [])
                sizes = [len(call.args[1]) for call in append.call_args_list]
                self.assertIn(4, sizes)
                self.assertEqual([v.sequence for v in await run.aoutput_events()], list(range(1, 7)))

    async def test_batch_engine_timeout_still_interrupts_and_next_request_runs(self):
        async def action(context):
            yield "partial"
            if context.messages[-1].content == "slow":
                await asyncio.sleep(10)
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={"test": BaseEngine(action=action)},
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=8))) as backend:
                session = await (await backend.projects.acreate(config={"policies": {
                    "run": {"timeout_seconds": .1}}})).sessions.acreate()
                first = await session.run.submit("slow", engine="test")
                second = await session.run.submit("fast", engine="test")
                self.assertEqual((await (await first.wait(timeout=5)).aresult()).status, RunStatus.FAILED)
                self.assertEqual((await (await second.wait(timeout=5)).aresult()).status, RunStatus.COMPLETED)


class BackupTests(unittest.IsolatedAsyncioTestCase):
    async def test_unversioned_backup_requires_explicit_transform(self):
        path = self.project.paths.root / "project.json"
        data = read_json(path)
        data.pop("storage_version")
        atomic_json(path, data)
        source = DirectoryBackups().create(self.project.paths.root, self.root / "old", metadata={})
        with self.assertRaisesRegex(ValueError, "storage_version"):
            await self.app.projects.arestore_backup(source)
        def upgrade(root):
            current = read_json(root / "project.json")
            current["storage_version"] = 1
            atomic_json(root / "project.json", current)
        converted = await self.app.projects.aupgrade_backup(source, self.root / "converted", transform=upgrade)
        self.assertNotIn("storage_version", read_json(source / "project" / "project.json"))
        self.assertEqual(read_json(converted / "project" / "project.json")["storage_version"], 1)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        async def work(context):
            yield "persisted"
        self.app = LargeLanguageModel(self.root / "source", engines={"test": BaseEngine(action=work)})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()
        self.session = await self.project.sessions.acreate()

    async def test_run_history_restore_and_no_overwrite(self):
        run = await (await self.session.run.submit("go", engine="test")).wait(timeout=10)
        with self.assertRaisesRegex(ValueError, "attached"):
            await self.project.abackup(self.root / "blocked")
        await self.session.run.shutdown()
        backup = await self.project.abackup(self.root / "backup")
        async with LargeLanguageModel(self.root / "destination") as destination:
            restored = await destination.projects.arestore_backup(backup)
            session = await restored.sessions.aload(self.session.id)
            saved = await session.run.aload(run.id)
            self.assertEqual((await saved.aresult()).output.text, "persisted")
            with self.assertRaises(FileExistsError):
                await destination.projects.arestore_backup(backup)

    async def test_tampered_backup_memory_and_link_rejected(self):
        backup = await self.project.abackup(self.root / "backup")
        path = backup / "project" / "project.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            DirectoryBackups().verify(backup)
        memory = await self.app.projects.acreate(conversation_storage="memory")
        with self.assertRaisesRegex(ValueError, "file conversation"):
            await memory.abackup(self.root / "memory")
        (self.project.paths.root / "linked").symlink_to(self.root / "outside")
        with self.assertRaises(ValueError):
            await self.project.abackup(self.root / "linked-backup")

    async def test_explicit_upgrade_copies_source_and_rolls_back_failure(self):
        backup = await self.project.abackup(self.root / "backup")
        original = (backup / "project" / "project.json").read_bytes()
        def broken(root):
            (root / "project.json").write_text("broken")
            raise RuntimeError("transform failed")
        with self.assertRaises(RuntimeError):
            await self.app.projects.aupgrade_backup(backup, self.root / "failed", transform=broken)
        self.assertFalse((self.root / "failed").exists())
        self.assertEqual((backup / "project" / "project.json").read_bytes(), original)
        def change(root):
            data = read_json(root / "project.json")
            data["config"].setdefault("data", {})["future_setting"] = True
            atomic_json(root / "project.json", data)
        upgraded = await self.app.projects.aupgrade_backup(backup, self.root / "upgraded", transform=change)
        DirectoryBackups().verify(upgraded)
        self.assertTrue(read_json(upgraded / "project" / "project.json")["config"]["data"]["future_setting"])
        self.assertEqual((backup / "project" / "project.json").read_bytes(), original)

    async def test_unknown_version_refuses_load_and_stale_repository_save(self):
        model = self.project.data
        path = self.project.paths.root / "project.json"
        data = read_json(path)
        data["storage_version"] = 99
        atomic_json(path, data)
        for operation in (lambda: self.project.data,
                          lambda: self.app.project_manager.repository.save(model)):
            with self.assertRaisesRegex(ValueError, "storage_version"):
                operation()
        self.assertEqual(read_json(path)["storage_version"], 99)

    async def test_rag_backup_restores_vector_graph_and_search_generation(self):
        from llm.components.rag import RAGComponent, EmbeddingModel
        from tests.llm.test_rag_components import fake_embedding
        from tests.llm.test_unified_rag import ChainExtractor
        component = RAGComponent(embedding=EmbeddingModel(model="test/embedding", embedding_fn=fake_embedding),
                                 extractor=ChainExtractor())
        async with LargeLanguageModel(self.root / "rag-source", components=[component]) as source:
            project = await source.projects.acreate(components=["rag"], config=rag_project())
            await project.components.rag.aadd_document(identifier="doc", title="guide",
                                                       content="needle Alice links Atlas")
            expected = await project.components.rag.asearch("needle", method="hybrid")
            backup = await project.abackup(self.root / "rag-backup")
        async with LargeLanguageModel(self.root / "rag-dest", components=[component]) as destination:
            restored = await destination.projects.arestore_backup(backup)
            self.assertEqual(await restored.components.rag.asearch("needle", method="hybrid"), expected)


class PolicyValidationTests(unittest.TestCase):
    def test_direct_managers_share_repository_and_support_backup(self):
        from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
        from llm.services.runtime.runs import RunManager
        from llm.engines.registry import EngineRegistry
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = ProjectManager(ProjectRepository(root / "projects"))
            project = manager.create("direct")
            session = manager.sessions.create(project, "session")
            runtime = RunManager(manager.sessions, EngineRegistry(), session=session)
            self.assertIs(runtime.repository, manager.sessions.run_repository)
            archive = manager.backup(project, root / "backup")
            DirectoryBackups().verify(archive)

    def test_batch_reduces_fsync_without_merging_individual_records(self):
        import os
        from llm.services.history.conversation import ConversationStore
        from llm.core.models import MessageRole, MessageStatus
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "conversation.jsonl")
            message = store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING)
            original = os.fsync
            with patch("llm.services.history.conversation.os.fsync", wraps=original) as sync:
                store.deltas(message.id, [(str(i), "append") for i in range(32)])
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(len(store.path.read_text().splitlines()), 33)
            self.assertEqual(ConversationStore(store.path).get(message.id).content, "".join(map(str, range(32))))

    def test_policy_and_index_limits(self):
        for build in (lambda: ProviderLimits(max_active=0), lambda: ProviderLimits(max_waiting=-1),
                      lambda: ProviderLimits(wait_seconds=float("nan")), lambda: OutputBuffer(batch_size=True),
                      lambda: OutputBuffer(max_delay=0), lambda: RunRepository(output_index_stride=-1)):
            with self.assertRaises(ValueError):
                build()
