from asyncio import timeout
import asyncio
import json
import logging
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus, ProjectConfig, SessionStatus
from llm.core.paths import ProjectPaths, SessionPaths
from llm.engines.registry import EngineRegistry
from llm.services.history.conversation import ConversationStore
from llm.services.infrastructure.storage import remove_owned_tree
from llm.services.infrastructure.logging import _RaisingFileHandler, log_event
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.lifecycle.sessions import SessionManager, SessionRuntime
from tests.llm.support.fake_engine import FakeStreamingEngine


def records(directory: Path) -> list[dict]:
    return [json.loads(line) for line in (directory / "service.log").read_text(
        encoding="utf-8").splitlines()]


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "projects"
        self.sessions = SessionManager()
        self.projects = ProjectManager(ProjectRepository(self.root), self.sessions)
        self.project = self.projects.create("Project")
        self.session = self.sessions.create(self.project, "Session")
        self.store = ConversationStore(self.session.paths.conversation)
        self.store.create(MessageRole.USER, "retained history", MessageStatus.QUEUED)

    def test_delete_defaults_to_reversible_metadata_change(self) -> None:
        stale = deepcopy(self.session)
        self.session.metadata["newer"] = True
        self.sessions.save(self.session)
        self.sessions.delete(stale)
        self.assertEqual(self.sessions.load(self.project, stale.id).metadata, {"newer": True})
        self.assertTrue(self.session.paths.conversation.is_file())
        self.sessions.restore(stale)
        self.assertEqual(self.sessions.load(self.project, stale.id).status, SessionStatus.IDLE)
        self.projects.delete(self.project)
        self.assertEqual(self.projects.list(), [])
        self.projects.restore(self.project)
        self.assertEqual(self.store.list()[0].content, "retained history")

    def test_permanent_session_delete_removes_descendants_preserves_sibling_and_parent_log(self) -> None:
        sibling = self.sessions.create(self.project, "Sibling")
        artifact = self.session.paths.root / "runs" / "nested" / "artifact.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("artifact", encoding="utf-8")
        self.sessions.delete(self.session, permanent=True)
        self.assertFalse(self.session.paths.root.exists())
        self.assertTrue(sibling.paths.root.exists())
        event = records(self.project.paths.logs)[-1]
        self.assertEqual((event["event"], event["entity_id"], event["permanent"]),
                         ("session.deleted", self.session.id, True))
        with self.assertRaises(FileNotFoundError):
            self.sessions.restore(self.session)
        self.assertFalse(self.session.paths.root.exists())

    def test_permanent_project_delete_removes_all_sessions_and_preserves_sibling(self) -> None:
        sibling = self.projects.create("Sibling")
        self.sessions.delete(self.session)
        self.projects.delete(self.project, permanent=True)
        self.assertFalse(self.project.paths.root.exists())
        self.assertTrue(sibling.paths.root.exists())
        self.assertEqual(records(self.root / "logs")[-1]["entity_id"], self.project.id)
        with self.assertRaises(FileNotFoundError):
            self.projects.restore(self.project)
        self.assertFalse(self.project.paths.root.exists())

    def test_permanent_requires_explicit_bool(self) -> None:
        for value in ("false", "true", 1, None):
            for manager, item in ((self.sessions, self.session), (self.projects, self.project)):
                with self.subTest(value=value, manager=type(manager).__name__):
                    with self.assertRaises(TypeError):
                        manager.delete(item, permanent=value)
        self.assertTrue(self.session.paths.root.exists())

    def test_failed_removal_does_not_report_success_or_mark_deleted(self) -> None:
        with patch("llm.services.infrastructure.transactions.os.rename", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                self.sessions.delete(self.session, permanent=True)
        self.assertEqual(self.session.status, SessionStatus.IDLE)
        self.assertTrue(self.session.paths.conversation.exists())
        self.assertFalse(any(item["event"] == "session.deleted" and item.get("permanent")
                             for item in records(self.project.paths.logs)))

    def test_permanent_delete_rejects_persisted_active_run(self) -> None:
        self.session.status = SessionStatus.RUNNING
        self.sessions.repository.save(self.session)
        for manager, item in ((self.sessions, self.session), (self.projects, self.project)):
            with self.assertRaises(ValueError):
                manager.delete(item, permanent=True)
        self.assertTrue(self.session.paths.root.exists())

    def test_forged_paths_and_ownership_are_rejected_without_removing_files(self) -> None:
        forged_project = deepcopy(self.project)
        forged_project.paths = ProjectPaths(self.root.parent)
        with self.assertRaises(ValueError):
            self.projects.delete(forged_project, permanent=True)
        forged_session = deepcopy(self.session)
        forged_session.paths = SessionPaths(self.project.paths.root)
        with self.assertRaises(ValueError):
            self.sessions.repository.delete(forged_session)
        forged_session = deepcopy(self.session)
        forged_session.project_id = "a" * 32
        with self.assertRaises(ValueError):
            self.sessions.repository.delete(forged_session)
        with self.assertRaises(ValueError):
            remove_owned_tree(self.root, self.root.parent, self.project.id)
        self.assertTrue(self.session.paths.conversation.is_file())

    def test_linked_descendant_preflight_rejects_before_removal(self) -> None:
        # 경로 사전 검증이 삭제보다 먼저 수행되는지 확인한다.
        linked = self.session.paths.root / "external"
        linked.mkdir()
        marker = linked / "marker"
        marker.write_text("must survive", encoding="utf-8")
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == linked or original(path)):
            with self.assertRaises(ValueError):
                self.sessions.delete(self.session, permanent=True)
        self.assertTrue(marker.exists())
        self.assertTrue(self.session.paths.conversation.exists())

    def test_logging_failure_does_not_fail_metadata_save(self) -> None:
        self.session.title = "Changed"
        with patch("llm.services.infrastructure.logging._RaisingFileHandler", side_effect=PermissionError("private")):
            with self.assertWarnsRegex(RuntimeWarning, "could not write an operational log"):
                self.sessions.save(self.session)
        self.assertEqual(self.sessions.load(self.project, self.session.id).title, "Changed")

    def test_log_rotation_and_no_duplicate_global_handlers(self) -> None:
        root_handlers = list(logging.getLogger().handlers)
        def small_handler(filename, **kwargs):
            return _RaisingFileHandler(filename, **{**kwargs, "maxBytes": 220, "backupCount": 3})
        with patch("llm.services.infrastructure.logging._RaisingFileHandler", side_effect=small_handler):
            for _ in range(12):
                log_event(self.session.paths.logs, "session.loaded", entity_id=self.session.id)
        files = list(self.session.paths.logs.glob("service.log*"))
        self.assertEqual(len(files), 4)
        self.assertEqual(logging.getLogger().handlers, root_handlers)
        self.assertTrue(all(json.loads(line)["event"] == "session.loaded"
                            for path in files for line in path.read_text(encoding="utf-8").splitlines()))

    def test_logging_never_recreates_deleted_domain(self) -> None:
        self.sessions.delete(self.session, permanent=True)
        log_event(self.session.paths.logs, "session.loaded", entity_id=self.session.id)
        self.assertFalse(self.session.paths.root.exists())


class RuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sessions = SessionManager()
        self.projects = ProjectManager(ProjectRepository(self.root), self.sessions)
        self.project = self.projects.create("private-project-title",
                                            config=ProjectConfig())
        self.session = self.sessions.create(self.project, "private-session-title")
        self.registry = EngineRegistry()
        self.registry.register("fake", FakeStreamingEngine(chunks=("private-answer",)))
        self.manager = RunManager(self.sessions, self.registry, session=self.session)
        self.addAsyncCleanup(self.manager.shutdown)

    async def test_queued_and_idle_attached_sessions_cannot_be_deleted_or_cloned(self) -> None:
        await self.manager.submit("private-prompt", engine="fake")
        for phase in ("queued", "idle"):
            if phase == "idle":
                await self.manager.wait_idle()
            for permanent in (False, True):
                for manager, item in ((self.sessions, self.session), (self.projects, self.project)):
                    with self.assertRaisesRegex(ValueError, "runtime is attached"):
                        manager.delete(item, permanent=permanent)
            with self.assertRaises(ValueError):
                self.sessions.clone(self.session, self.project)
        await self.manager.shutdown()
        self.sessions.delete(self.session, permanent=True)
        self.assertFalse(self.session.paths.root.exists())

    async def test_second_manager_cannot_recover_an_attached_session(self) -> None:
        await self.manager.start()
        other = RunManager(self.sessions, self.registry, session=self.session)
        self.addAsyncCleanup(other.shutdown)
        with self.assertRaisesRegex(ValueError, "already has an attached runtime"):
            await other.start()
        await self.manager.shutdown()
        await other.start()
        # Repeated shutdown on the old owner must not release the new owner.
        await self.manager.shutdown()
        with self.assertRaises(ValueError):
            self.sessions.delete(self.session, permanent=True)

    async def test_recovery_failure_releases_runtime_attachment(self) -> None:
        with patch.object(self.manager, "_recover", side_effect=ValueError("broken")):
            with self.assertRaises(ValueError):
                await self.manager.start()
        self.sessions.delete(self.session)

    async def test_failed_and_interrupted_runs_have_terminal_domain_logs(self) -> None:
        self.registry.register("broken", FakeStreamingEngine(fail_after=0))
        await self.manager.submit("private-prompt", engine="broken")
        await self.manager.wait_idle()
        failed_run, = self.manager.repository.list(self.session)
        failed_step, = self.manager.steps.list(failed_run)
        self.assertIn("run.failed", [row["event"] for row in records(failed_run.paths.logs)])
        self.assertIn("step.failed", [row["event"] for row in records(failed_step.paths.logs)])

        self.registry.register("blocked", FakeStreamingEngine(gate=asyncio.Event()))
        await self.manager.submit("private-prompt", engine="blocked")
        async with timeout(10):
            while not any(message.status == MessageStatus.STREAMING and message.content
                          for message in ConversationStore(self.session.paths.conversation).list()):
                await asyncio.sleep(0.001)
        self.assertTrue(await self.manager.interrupt())
        await self.manager.wait_idle()
        interrupted = self.manager.repository.list(self.session)[-1]
        step, = self.manager.steps.list(interrupted)
        self.assertIn("run.interrupted", [row["event"] for row in records(interrupted.paths.logs)])
        self.assertIn("step.interrupted", [row["event"] for row in records(step.paths.logs)])

    async def test_each_domain_records_lifecycle_without_conversation_or_metadata(self) -> None:
        self.session.metadata = {"Authorization": "private-authorization"}
        self.sessions.save(self.session)
        await self.manager.submit("private-prompt", engine="fake")
        await self.manager.wait_idle()
        run, = self.manager.repository.list(self.session)
        step, = self.manager.steps.list(run)
        for model, event in ((self.project, "project.created"), (self.session, "request.queued"),
                             (run, "run.completed"), (step, "step.completed")):
            self.assertIn(event, [item["event"] for item in records(model.paths.logs)])
        all_logs = "\n".join(path.read_text(encoding="utf-8")
                             for path in self.root.rglob("service.log*"))
        for secret in ("private-project-title", "private-session-title", "private-authorization",
                       "private-prompt", "private-answer"):
            self.assertNotIn(secret, all_logs)
        self.assertEqual(SessionRuntime.__module__, "llm.services.lifecycle.sessions")
