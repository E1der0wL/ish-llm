"""복합 저장의 실제 실패·프로세스 종료·재복구와 도메인 경계를 검증한다."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus, RunStatus, SessionStatus
from llm.engines.base import EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from llm.services.history.conversation import ConversationStore, MemoryConversationStore
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.storage import atomic_json, append_bytes, read_json, remove_named_tree
from llm.services.infrastructure.transactions import current_transaction, after_commit
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.lifecycle.sessions import SessionRuntime
from llm.services.runtime.runs import RunManager


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owner = WorkspaceOwnership(self.root)

    def process(self, body, *, root=None):
        code = """
import os, sys
from pathlib import Path
from llm.services.infrastructure.locking import WorkspaceOwnership
from llm.services.infrastructure.storage import atomic_json, append_bytes, remove_named_tree
from llm.services.infrastructure.transactions import current_transaction
root = Path(sys.argv[1]); owner = WorkspaceOwnership(root)
""" + textwrap.dedent(body)
        return subprocess.run([sys.executable, "-c", code, str(root or self.root)], capture_output=True, text=True)

    def test_crash_at_each_fsync_keeps_all_old_or_all_new(self):
        # 모든 fsync 뒤에서 실제 프로세스를 종료한다. journal/commit/GC 중단을 포함한다.
        saw_commit = False
        for cut in range(1, 80):
            root = self.root / str(cut)
            root.mkdir()
            atomic_json(root / "a.json", {"value": "old"})
            (root / "history").write_bytes(b"old\n")
            (root / "tree").mkdir()
            (root / "tree" / "data").write_text("original")
            body = '''
sync = os.fsync
count = 0
def crash(fd):
    global count
    sync(fd)
    count += 1
    if count == CUT:
        os._exit(75)
os.fsync = crash
with owner.scope():
    atomic_json(root / "a.json", {"value": "new"})
    append_bytes(root / "history", b"new\\n")
    remove_named_tree(root, root / "tree", "tree")
    atomic_json(root / "receipt.json", {"done": True})
'''.replace("CUT", str(cut))
            result = self.process(body, root=root)
            self.assertIn(result.returncode, (0, 75), result.stderr)
            owner = WorkspaceOwnership(root)
            with owner.scope():
                changed = read_json(root / "a.json")["value"] == "new"
                saw_commit |= changed
                self.assertEqual((root / "history").read_bytes(), b"old\nnew\n" if changed else b"old\n", cut)
                self.assertEqual((root / "receipt.json").exists(), changed, cut)
                self.assertEqual((root / "tree").exists(), not changed, cut)
            if result.returncode == 0:
                break
        else:
            self.fail("fault matrix did not reach the final fsync")
        self.assertTrue(saw_commit)

    def test_nested_writes_and_jsonl_roll_back_without_copying_history(self):
        path = self.root / "history.jsonl"
        path.write_bytes(b"old\n" * 100000)
        original = path.read_bytes()
        atomic_json(self.root / "a.json", {"value": "old"})
        observed = []
        with self.assertRaisesRegex(RuntimeError, "failed"):
            with self.owner.scope():
                atomic_json(self.root / "a.json", {"value": "new"})
                with self.owner.scope():
                    append_bytes(path, b"new\n")
                    atomic_json(self.root / "new" / "b.json", {"value": 1})
                journal = current_transaction().path
                self.assertLess(sum(p.stat().st_size for p in journal.iterdir()), 4096)
                after_commit(lambda: observed.append("visible"))
                raise RuntimeError("failed")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(read_json(self.root / "a.json"), {"value": "old"})
        self.assertFalse((self.root / "new").exists())
        self.assertEqual(observed, [])
        with self.owner.scope():
            atomic_json(self.root / "a.json", {"value": "committed"})
            after_commit(lambda: observed.append(read_json(self.root / "a.json")))
            self.assertEqual(observed, [])
        self.assertEqual(observed, [{"value": "committed"}])

    def test_process_death_before_commit_recovers_before_first_read(self):
        atomic_json(self.root / "a.json", {"value": "old"})
        (self.root / "history").write_bytes(b"old\n")
        result = self.process('''
with owner.scope():
    atomic_json(root / "a.json", {"value": "new"})
    append_bytes(root / "history", b"new\\n")
    atomic_json(root / "new" / "run.json", {"x": 1})
    os._exit(71)
''')
        self.assertEqual(result.returncode, 71, result.stderr)
        with self.owner.scope():
            self.assertEqual(read_json(self.root / "a.json"), {"value": "old"})
            self.assertEqual((self.root / "history").read_bytes(), b"old\n")
            self.assertFalse((self.root / "new").exists())
        with self.owner.scope():
            self.assertEqual(list(self.owner.transactions.journal.iterdir()), [])

    def test_process_death_after_commit_keeps_changes_and_finishes_gc(self):
        (self.root / "tree").mkdir()
        (self.root / "tree" / "data").write_text("large payload")
        result = self.process('''
owner.transactions._retire = lambda path: os._exit(72)
with owner.scope():
    remove_named_tree(root, root / "tree", "tree")
    atomic_json(root / "receipt.json", {"deleted": True})
''')
        self.assertEqual(result.returncode, 72, result.stderr)
        with self.owner.scope():
            self.assertFalse((self.root / "tree").exists())
            self.assertEqual(read_json(self.root / "receipt.json"), {"deleted": True})
        self.assertEqual(list(self.owner.transactions.journal.iterdir()), [])

    def test_recovery_itself_can_die_and_resume(self):
        (self.root / "history").write_bytes(b"old\n")
        result = self.process('''
with owner.scope():
    append_bytes(root / "history", b"one\\n")
    append_bytes(root / "history", b"two\\n")
    atomic_json(root / "history", {"temporary": True})
    os._exit(73)
''')
        self.assertEqual(result.returncode, 73, result.stderr)
        result = self.process('''
import llm.services.infrastructure.transactions as tx
write = tx._write
def fail(path, data):
    write(path, data)
    if path.name.startswith("undone-"):
        os._exit(74)
tx._write = fail
with owner.scope():
    pass
''')
        self.assertEqual(result.returncode, 74, result.stderr)
        with self.owner.scope():
            self.assertEqual((self.root / "history").read_bytes(), b"old\n")

    def test_deleted_tree_is_restored_on_abort(self):
        (self.root / "tree").mkdir()
        (self.root / "tree" / "data").write_bytes(b"original")
        with self.assertRaises(OSError):
            with self.owner.scope():
                remove_named_tree(self.root, self.root / "tree", "tree")
                self.assertFalse((self.root / "tree").exists())
                raise OSError("second write failed")
        self.assertEqual((self.root / "tree" / "data").read_bytes(), b"original")

    def test_create_delete_recreate_in_same_transaction_is_reversible(self):
        with self.assertRaisesRegex(OSError, "abort"):
            with self.owner.scope():
                atomic_json(self.root / "new" / "value.json", {"round": 1})
                remove_named_tree(self.root, self.root / "new", "new")
                atomic_json(self.root / "new" / "value.json", {"round": 2})
                atomic_json(self.root / "new" / "value.json", {"round": 3})
                raise OSError("abort")
        self.assertFalse((self.root / "new").exists())
        with self.owner.scope():
            self.assertEqual(list(self.owner.transactions.journal.iterdir()), [])

    def test_caught_nested_write_failure_cannot_commit_partial_state(self):
        with self.assertRaisesRegex(RuntimeError, "aborted"):
            with self.owner.scope():
                atomic_json(self.root / "new.json", {"value": "first"})
                try:
                    with self.owner.scope():
                        # 같은 신규 파일의 덮어쓰기도 부분 성공을 허용하지 않는다.
                        atomic_json(self.root / "new.json", {"value": "partial"})
                        raise OSError("nested failed")
                except OSError:
                    pass
        self.assertFalse((self.root / "new.json").exists())

    def test_file_and_memory_projection_rollback(self):
        for store in (ConversationStore(self.root / "conversation.jsonl"), MemoryConversationStore()):
            with self.subTest(store=type(store).__name__):
                with self.owner.scope():
                    message = store.create(MessageRole.USER, "original", MessageStatus.QUEUED)
                with self.assertRaises(OSError):
                    with self.owner.scope():
                        store.bind_run(message.id, "a" * 32)
                        store.set_status(message.id, MessageStatus.COMMITTED)
                        store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING)
                        raise OSError("session write failed")
                self.assertEqual(len(store.list()), 1)
                self.assertEqual(store.get(message.id).status, MessageStatus.QUEUED)
                self.assertIsNone(store.get(message.id).run_id)

    def test_partial_tail_repair_can_be_rolled_back(self):
        store = ConversationStore(self.root / "conversation.jsonl")
        store.create(MessageRole.USER, "old", MessageStatus.QUEUED)
        with store.path.open("ab") as stream:
            stream.write(b'{"partial":')
        original = store.path.read_bytes()
        with self.assertRaises(OSError):
            with self.owner.scope():
                store.create(MessageRole.USER, "new", MessageStatus.QUEUED)
                raise OSError("abort")
        self.assertEqual(store.path.read_bytes(), original)
        with self.owner.scope():
            store.create(MessageRole.USER, "new", MessageStatus.QUEUED)
        self.assertEqual([m.content for m in store.list()], ["old", "new"])

    def test_malicious_recovery_path_fails_closed(self):
        journal = self.root / ".transactions" / ("a" * 32)
        journal.mkdir(parents=True)
        (journal / "00000000.json").write_text(json.dumps({"kind": "create", "path": "../outside"}))
        with self.assertRaises(ValueError):
            with self.owner.scope():
                self.fail("corrupt recovery must prevent reads")


class DomainTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.projects = ProjectManager(ProjectRepository(self.root / "projects"))
        self.project = self.projects.create("project")
        self.sessions = self.projects.sessions
        self.session = self.sessions.create(self.project, "session")
        self.manager = RunManager(self.sessions, EngineRegistry(), session=self.session)
        self.runtime = SessionRuntime(self.project, self.session)
        self.store = self.manager._store(self.session)
        with self.projects.ownership.scope():
            self.message = self.store.create(MessageRole.USER, "request", MessageStatus.QUEUED,
                                             metadata={"engine": "explicit"})

    def begin(self):
        if not self.projects.ownership.session_attached((self.session.project_id, self.session.id)):
            self.sessions.attach_runtime(self.session)
            self.addCleanup(self.sessions.detach_runtime, self.session)
        with self.projects.ownership.scope():
            return self.manager._begin(self.runtime, self.message)

    def test_run_begin_failure_leaves_request_queued_and_no_orphans(self):
        with patch.object(self.sessions, "_save_runtime", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.begin()
        self.assertEqual(self.manager.repository.list(self.session), [])
        self.assertEqual(self.store.get(self.message.id).status, MessageStatus.QUEUED)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.session.status, SessionStatus.IDLE)
        self.assertIsNone(self.session.current_run_id)
        run = self.begin()
        self.assertEqual(self.store.get(self.message.id).run_id, run.id)

    def test_finish_failure_rolls_back_step_run_session_and_assistant(self):
        from llm.services.infrastructure.transactions import watch
        run = self.begin()
        with self.projects.ownership.scope():
            step = self.manager.steps.create(run, "tool", "external")
            self.manager.steps.start(step)
        with patch.object(self.sessions, "_save_runtime", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                with self.projects.ownership.scope():
                    watch(run)
                    self.manager._finish(self.runtime, run, RunStatus.FAILED, "failure")
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(self.manager.repository.load(self.session, run.id).status, RunStatus.RUNNING)
        self.assertEqual(self.manager.steps.load(run, step.id).status, "running")
        self.assertEqual(self.store.get(run.assistant_message_id).status, MessageStatus.STREAMING)
        with self.projects.ownership.scope():
            self.manager._finish(self.runtime, run, RunStatus.INTERRUPTED)
        self.assertEqual(self.sessions.load(self.project, self.session.id).status, SessionStatus.IDLE)

    def test_checkpoint_initialization_and_run_link_are_indivisible(self):
        run = self.begin()
        event = EngineEvent(EngineEventType.CHECKPOINT, metadata={"name": "graph", "operation": "initialize",
                                                                 "header": {}, "records": {}})
        from llm.services.infrastructure.transactions import watch
        with patch.object(self.manager.repository, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                with self.projects.ownership.scope():
                    watch(run)
                    self.manager.repository.record_checkpoint(run, event)
        self.assertNotIn("checkpoints", run.metadata)
        with self.assertRaises(FileNotFoundError):
            self.manager.repository.checkpoint(run)

    def test_project_clone_failure_publishes_no_partial_tree(self):
        with patch.object(self.sessions, "clone", side_effect=OSError("clone failure")):
            with self.assertRaises(OSError):
                self.projects.clone(self.project)
        self.assertEqual([p.id for p in self.projects.list(include_deleted=True)], [self.project.id])
        self.assertEqual([p.name for p in self.projects.repository.root.iterdir() if len(p.name) == 32],
                         [self.project.id])

    def test_project_deletion_failure_restores_data_and_handle(self):
        original = self.projects.repository.delete
        def fail(project):
            original(project)
            raise OSError("after removal")
        with patch.object(self.projects.repository, "delete", side_effect=fail):
            with self.assertRaises(OSError):
                self.projects.delete(self.project, permanent=True)
        self.assertFalse(self.project.deleted)
        self.assertEqual(self.sessions.load(self.project, self.session.id).id, self.session.id)

    def test_restore_publication_failure_removes_staging_and_destination(self):
        backup = self.projects.backup(self.project, self.root / "backup")
        original = (backup / "project" / "project.json").read_bytes()
        self.projects.delete(self.project, permanent=True)
        with patch.object(self.projects.repository, "load", side_effect=OSError("after publication")):
            with self.assertRaises(OSError):
                self.projects.restore_backup(backup)
        self.assertFalse(self.project.paths.root.exists())
        self.assertEqual((backup / "project" / "project.json").read_bytes(), original)
        self.assertEqual(self.projects.list(), [])
        self.assertEqual(list(self.projects.ownership.transactions.journal.iterdir()), [])
        restored = self.projects.restore_backup(backup)
        self.assertEqual(restored.id, self.project.id)

    def test_enabling_component_failure_preserves_selection_and_directories(self):
        from llm.components.base import Component
        class Broken(Component):
            name = directory = "broken"
            def initialize(self, project):
                (self.root(project) / "custom").mkdir(parents=True)
                (self.root(project) / "custom" / "file").write_text("partial")
                raise OSError("initialization failed")
        self.projects.components.register(Broken())
        with self.assertRaises(OSError):
            self.projects.set_components(self.project, ("broken",))
        self.assertEqual(self.projects.load(self.project.id).components, ())
        self.assertEqual(self.project.components, ())
        self.assertFalse((self.project.paths.root / "broken").exists())


class AsyncTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from llm.llm import LargeLanguageModel
        from llm.engines.base import BaseEngine
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        async def action(context):
            yield "streamed text"
        self.events = []
        self.app = LargeLanguageModel(self.temp.name, engines={"test": BaseEngine(action=action)},
                                     on_event=lambda run, event: self.events.append(event))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()
        self.session = await self.project.sessions.acreate()

    async def test_delta_failure_rolls_back_journal_and_does_not_notify(self):
        original = ConversationStore.delta
        def fail(store, *args, **kwargs):
            original(store, *args, **kwargs)
            raise OSError("conversation fsync failed")
        with patch.object(ConversationStore, "delta", fail):
            run = await (await self.session.run.submit("request", engine="test")).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual((await run.aresponse()).content, "")
        self.assertEqual(await run.aoutput_events(), [])
        self.assertNotIn(EngineEventType.TEXT_DELTA, [e.type for e in self.events])

    async def test_final_step_failure_preserves_only_committed_deltas(self):
        original = self.app.step_manager.save
        def fail(step):
            if step.status == "completed":
                raise OSError("step completion failed")
            return original(step)
        with patch.object(self.app.step_manager, "save", fail):
            run = await (await self.session.run.submit("request", engine="test")).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.FAILED)
        outputs = await run.aoutput_events()
        self.assertEqual([o.sequence for o in outputs], [1])
        self.assertEqual((await run.aresponse()).content, "streamed text")
        self.assertNotIn(EngineEventType.STEP_COMPLETED, [e.type for e in self.events])

    async def test_recovery_commit_failure_releases_runtime_claim(self):
        import llm.services.infrastructure.transactions as transactions
        original = transactions._write
        def fail(path, data):
            if path.name == "COMMITTED":
                raise OSError("commit unavailable")
            return original(path, data)
        manager = self.app._manager(self.session.data)
        with patch.object(transactions, "_write", fail):
            with self.assertRaises(OSError):
                await manager.start()
        self.assertEqual(self.app.project_manager.ownership.attached_sessions, ())
        run = await (await self.session.run.submit("retry start", engine="test")).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.COMPLETED)

    async def test_uncertain_commit_error_releases_runtime_claim_without_undoing_disk(self):
        import llm.services.infrastructure.transactions as transactions
        original = transactions._write
        def fail(path, data):
            original(path, data)
            if path.name == "COMMITTED":
                raise OSError("commit directory sync outcome unknown")
        manager = self.app._manager(self.session.data)
        with patch.object(transactions, "_write", fail):
            with self.assertRaises(OSError):
                await manager.start()
        self.assertEqual(self.app.project_manager.ownership.attached_sessions, ())
        run = await (await self.session.run.submit("retry start", engine="test")).wait(timeout=10)
        self.assertEqual(run.data.status, RunStatus.COMPLETED)
