"""Behavior tests for the host-facing backend facade."""

import asyncio
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.llm import LargeLanguageModel
from llm.components import ComponentRegistry
from llm.components.tools import ToolComponent, ToolData
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import MessageStatus, RunStatus
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.infrastructure.locking import WorkspaceOwnership
from tests.llm.support.fake_engine import FakeStreamingEngine


class ProjectSetupTests(unittest.TestCase):
    def test_auto_sessions_and_component_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProjectManager(ProjectRepository(Path(tmp)),
                                     components=[ToolComponent(), WorkflowComponent()])
            project = manager.create("project", components=["tools"])
            session = manager.sessions.create(project, "session")
            self.assertEqual(manager.sessions.load(project, session.id), session)
            self.assertIsInstance(manager.components, ComponentRegistry)
            self.assertTrue((project.paths.root / "tools").is_dir())
            self.assertFalse((project.paths.root / "workflows").exists())

    def test_invalid_registration_does_not_create_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "new"
            with self.assertRaises(ValueError):
                ProjectManager(ProjectRepository(root), components=[ToolComponent(), ToolComponent()])
            self.assertFalse(root.exists())

    def test_construction_has_no_storage_or_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "new"
            first, second = LargeLanguageModel(root), LargeLanguageModel(root)
            self.assertFalse(root.exists())
            self.assertIsNot(first.project_manager.sessions, second.project_manager.sessions)
            self.assertIsNot(first.project_manager.components.get("tools"),
                             second.project_manager.components.get("tools"))

    def test_cross_loop_execution_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = LargeLanguageModel(tmp, engines={})
            first, second = asyncio.new_event_loop(), asyncio.new_event_loop()
            async def bind():
                app._bind_loop()
            try:
                first.run_until_complete(bind())
                with self.assertRaisesRegex(RuntimeError, "original event loop"):
                    second.run_until_complete(bind())
                first.run_until_complete(app.shutdown())
            finally:
                first.close()
                second.close()


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = FakeStreamingEngine()
        self.events = []
        self.app = LargeLanguageModel(self.tmp.name, engines={"loop": self.engine},
                                     components=[ToolComponent(), WorkflowComponent()],
                                     on_run_event=self.events.append)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = self.app.projects.create("project")
        self.session = self.project.sessions.create()

    async def test_chain_reuses_runtime_and_exposes_run_step_history(self):
        loaded = self.app.projects.load(self.project.id).sessions.load(self.session.id)
        await asyncio.gather(self.session.run.submit("one", engine="loop"), loaded.run.submit("two", engine="loop"))
        await loaded.run.wait_idle()
        self.assertEqual(len(self.app._managers), 1)
        self.assertEqual(self.engine.max_active, 1)
        runs = loaded.run.list()
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs))
        run = loaded.run.load(runs[0].id)
        self.assertEqual(run.engine, "loop")
        steps = run.steps.list()
        self.assertEqual(len(steps), 1)
        self.assertEqual(run.steps.load(steps[0].id), steps[0])
        self.assertEqual(len(self.events), 4)
        self.assertEqual(len(loaded.conversation()), 4)
        # Service handles never enter serializable Project/Session models.
        self.assertNotIn("run", asdict(loaded.data))
        self.assertNotIn("app", asdict(self.project.data))

    async def test_facade_requires_engine_even_when_only_one_is_registered(self):
        with self.assertRaises(TypeError):
            await self.session.run.submit("missing")
        self.assertEqual(self.app._managers, {})
        self.assertEqual(self.session.conversation(), [])

    async def test_multiple_sessions_execute_concurrently(self):
        self.engine.gate = asyncio.Event()
        other = self.project.sessions.create("other")
        await asyncio.gather(self.session.run.submit("one", engine="loop"), other.run.submit("two", engine="loop"))
        async with asyncio.timeout(10):
            while self.engine.max_active < 2:
                await asyncio.sleep(0.01)
        self.engine.gate.set()
        await asyncio.gather(self.session.run.wait_idle(), other.run.wait_idle())
        self.assertEqual(self.engine.max_active, 2)

    async def test_component_registration_does_not_require_handlers(self):
        self.project.components.select(["tools", "workflows"])
        tools = self.project.components.tools
        self.assertIsInstance(tools, ToolData)
        self.assertIsInstance(self.project.components["tools"], ToolData)
        definition = {"source": "raise RuntimeError('must not import during CRUD')"}
        identifier = tools.create(definition, identifier="search")
        self.assertEqual(identifier, "search")
        self.assertEqual(tools.load(identifier), definition)
        tools.enable("search")
        self.assertEqual(self.project.components["tools"].enabled(), ["search"])
        tools.disable("search")
        tools.set_enabled(["search"])
        self.assertFalse(hasattr(self.app.project_manager.components.get("tools"), "catalog"))
        workflow = self.project.components.workflows.create(WorkflowGraph(entry="end", custom=42).node("end", "end").to_dict())
        self.assertEqual(self.project.components["workflows"].load(workflow)["custom"], 42)
        self.project.components.remove("tools")
        with self.assertRaises(ValueError):
            tools.create(definition, identifier="another")
        with self.assertRaises(ValueError):
            tools.enable("search")

    async def test_project_and_session_lifecycle(self):
        self.project.save(title="renamed", config={"custom": 7, "parameters": {"engines": {"loop": {"completion": {"model": "test"}}}}})
        self.session.save(title="new title", config={"custom": 8})
        self.assertEqual(self.app.projects.load(self.project.id).data.title, "renamed")
        self.assertEqual(self.project.sessions.load(self.session.id).data.config["custom"], 8)
        cloned_session = self.session.clone()
        self.assertNotEqual(cloned_session.id, self.session.id)
        cloned_session.delete()
        self.assertEqual(len(self.project.sessions.list()), 1)
        cloned_session.restore()
        cloned_session.delete(permanent=True)
        with self.assertRaises(FileNotFoundError):
            self.project.sessions.load(cloned_session.id)
        clone = self.project.clone(title="copy")
        self.assertEqual(len(clone.sessions.list()), 1)
        clone.delete()
        self.assertEqual(len(self.app.projects.list()), 1)
        clone.restore()
        clone.delete(permanent=True)

    async def test_read_history_checks_ownership(self):
        await self.session.run.submit("one", engine="loop")
        await self.session.run.wait_idle()
        run = self.session.run.list()[0]
        other = self.project.sessions.create()
        with self.assertRaises(FileNotFoundError):
            other.run.load(run.id)
        with self.assertRaises(ValueError):
            self.session.run.load("../escape")
        await self.session.run.shutdown()
        self.session.delete(permanent=True)
        with self.assertRaises(FileNotFoundError):
            run.steps.list()

    async def test_shutdown_one_session_then_resume(self):
        await self.session.run.submit("one", engine="loop")
        await self.session.run.wait_idle()
        with self.assertRaises(ValueError):
            self.session.delete()
        await self.session.run.shutdown()
        self.session.save(title="detached")
        await self.project.sessions.load(self.session.id).run.submit("two", engine="loop")
        await self.session.run.wait_idle()
        self.assertEqual(len(self.session.run.list()), 2)

    async def test_shutdown_preserves_queue_for_new_backend(self):
        self.engine.gate = asyncio.Event()
        await self.session.run.submit("active", engine="loop")
        async with asyncio.timeout(10):
            while not self.engine.active:
                await asyncio.sleep(0.01)
        await self.session.run.submit("queued", engine="loop")
        await self.app.shutdown()
        with self.assertRaises(RuntimeError):
            await self.session.run.submit("closed", engine="loop")
        async with LargeLanguageModel(self.tmp.name, engines={"loop": FakeStreamingEngine()}) as fresh:
            session = fresh.projects.load(self.project.id).sessions.load(self.session.id)
            self.assertIn(MessageStatus.QUEUED, [m.status for m in session.conversation()])
            await session.run.start()
            await session.run.wait_idle()
            self.assertEqual([r.data.status for r in session.run.list()],
                             [RunStatus.INTERRUPTED, RunStatus.COMPLETED])

    async def test_context_manager_closes_and_releases_ownership(self):
        async with self.app:
            await self.session.run.submit("one", engine="loop")
            await self.session.run.wait_idle()
        with WorkspaceOwnership(Path(self.tmp.name) / "projects").scope():
            pass
        with self.assertRaises(RuntimeError):
            self.app.projects.create()

    async def test_shutdown_attempts_all_managers_when_one_fails(self):
        other = self.project.sessions.create()
        await self.session.run.start()
        await other.run.start()
        first = self.app._managers[(self.project.id, self.session.id)]
        original = first.shutdown
        async def failure():
            await original()
            raise OSError("test storage error")
        with patch.object(first, "shutdown", side_effect=failure):
            with self.assertRaises(OSError):
                await self.app.shutdown()
        self.assertFalse(self.app.project_manager.ownership.session_attached((self.project.id, other.id)))

    async def test_cancelled_shutdown_still_drains_all_managers(self):
        await self.session.run.start()
        manager = self.app._managers[(self.project.id, self.session.id)]
        original = manager.shutdown
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed():
            entered.set()
            await release.wait()
            await original()
        with patch.object(manager, "shutdown", side_effect=delayed):
            stopping = asyncio.create_task(self.app.shutdown())
            await entered.wait()
            stopping.cancel()
            await asyncio.sleep(0)
            self.assertFalse(stopping.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await stopping
        self.assertFalse(self.app.project_manager.ownership.session_attached((self.project.id, self.session.id)))


if __name__ == "__main__":
    unittest.main()
