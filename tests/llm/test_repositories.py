import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from llm.core.models import Run, StepStatus, SessionStatus, new_id
from llm.engines.registry import EngineRegistry
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager, RunRepository
from llm.services.lifecycle.steps import StepManager, StepRepository
from llm.services.infrastructure.storage import atomic_json, read_json
from llm.services.lifecycle.sessions import SessionManager, SessionRepository


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session_repository = SessionRepository()
        self.sessions = SessionManager(repository=self.session_repository)
        self.projects = ProjectManager(ProjectRepository(self.root), self.sessions)
        self.project = self.projects.create("Project")
        self.session = self.sessions.create(self.project, "Session")
        self.runs = RunRepository()
        run_id = new_id()
        self.run = Run(run_id, self.session.id, new_id(), new_id(), "fake",
                       self.runs.paths(self.session, run_id))
        self.runs.save(self.run)
        self.steps = StepManager(repository=StepRepository())
        self.step = self.steps.create(self.run, "llm", "Completion")

    def test_all_metadata_repositories_round_trip_without_runtime_paths(self) -> None:
        cases = (
            (self.project, "project.json", lambda: self.projects.repository.load(self.project.id)),
            (self.session, "session.json", lambda: self.session_repository.load(self.project, self.session.id)),
            (self.run, "run.json", lambda: self.runs.load(self.session, self.run.id)),
            (self.step, "step.json", lambda: self.steps.repository.load(self.run, self.step.id)),
        )
        for model, filename, load in cases:
            with self.subTest(filename=filename):
                self.assertEqual(load(), model)
                data = read_json(model.paths.root / filename)
                self.assertNotIn("paths", data)
                self.assertNotIn("queue", data)
                self.assertEqual(data["id"], model.id)

    def test_repositories_reject_wrong_id_or_parent(self) -> None:
        cases = (
            (self.project, "project.json", ("id",), lambda: self.projects.load(self.project.id)),
            (self.session, "session.json", ("id", "project_id"),
             lambda: self.session_repository.load(self.project, self.session.id)),
            (self.run, "run.json", ("id", "session_id"), lambda: self.runs.load(self.session, self.run.id)),
            (self.step, "step.json", ("id", "run_id"),
             lambda: self.steps.repository.load(self.run, self.step.id)),
        )
        for model, filename, keys, load in cases:
            path = model.paths.root / filename
            original = read_json(path)
            for key in keys:
                with self.subTest(filename=filename, key=key):
                    atomic_json(path, {**original, key: new_id()})
                    with self.assertRaises(ValueError):
                        load()
                    atomic_json(path, original)

    def test_session_repository_lists_filters_and_reloads(self) -> None:
        stale = deepcopy(self.session)
        self.session.metadata = {"saved": True}
        self.sessions.save(self.session)
        self.assertEqual(self.session_repository.reload(stale).metadata, {"saved": True})
        other = self.sessions.create(self.project, "Deleted session")
        self.sessions.delete(other)
        self.assertEqual(self.session_repository.list(self.project), [self.session])
        self.assertEqual(len(self.session_repository.list(self.project, include_deleted=True)), 2)

    def test_session_manager_lifecycle_uses_injected_repository_without_json(self) -> None:
        repository = Mock(spec=SessionRepository)
        repository.paths.side_effect = SessionRepository().paths
        stored = {}
        repository.save.side_effect = lambda session: stored.update({session.id: deepcopy(session)})
        repository.reload.side_effect = lambda session: deepcopy(stored[session.id])
        repository.load.side_effect = lambda project, session_id: deepcopy(stored[session_id])
        repository.list.side_effect = lambda project, include_deleted=False: [
            deepcopy(session) for session in stored.values()
            if include_deleted or session.status != SessionStatus.DELETED]
        manager = SessionManager(repository=repository, project_access=self.projects.access)
        session = manager.create(self.project, "In memory")
        repository.initialize.assert_called_once_with(self.project)
        self.assertFalse(session.paths.root.exists())
        self.assertEqual(manager.load(self.project, session.id), session)
        self.assertEqual(manager.list(self.project), [session])
        clone = manager.clone(session, self.project)
        self.assertNotEqual(clone.id, session.id)
        manager.delete(session)
        self.assertEqual(stored[session.id].status, SessionStatus.DELETED)
        manager.restore(session)
        self.assertEqual(stored[session.id].status, SessionStatus.IDLE)
        # The inactive guard must consult the repository, not a session.json path
        # or the caller's stale object. No file exists for this Session.
        stored[session.id].status = SessionStatus.RUNNING
        with self.assertRaises(ValueError):
            manager.require_inactive(session)
        repository.reload.assert_called_with(session)

    def test_step_manager_lifecycle_uses_injected_repository_without_json(self) -> None:
        repository = Mock(spec=StepRepository)
        repository.paths.side_effect = StepRepository().paths
        stored = {}
        repository.exists.side_effect = lambda run, step_id: step_id in stored
        repository.save.side_effect = lambda step: stored.update({step.id: deepcopy(step)})
        repository.load.side_effect = lambda run, step_id: deepcopy(stored[step_id])
        repository.list.side_effect = lambda run: [deepcopy(step) for step in stored.values()]
        manager = StepManager(repository=repository)
        step = manager.create(self.run, "tool", "In memory")
        self.assertFalse(step.paths.root.exists())
        manager.start(step)
        manager.complete(step)
        self.assertEqual(manager.load(self.run, step.id).status, StepStatus.COMPLETED)
        with self.assertRaises(ValueError):
            manager.create(self.run, "tool", "Duplicate", step_id=step.id)
        pending = manager.create(self.run, "llm", "Pending")
        manager.recover(self.run)
        self.assertEqual(manager.load(self.run, pending.id).status, StepStatus.INTERRUPTED)
        self.assertEqual(manager.load(self.run, step.id).status, StepStatus.COMPLETED)

    def test_step_repository_validates_paths_and_duplicate_identity(self) -> None:
        self.assertTrue(self.steps.repository.exists(self.run, self.step.id))
        self.assertEqual(self.steps.repository.list(self.run), [self.step])
        with self.assertRaises(ValueError):
            self.steps.create(self.run, "llm", "Duplicate", step_id=self.step.id)
        with self.assertRaises(ValueError):
            self.steps.repository.paths(self.run, "../outside")
        with self.assertRaises(ValueError):
            self.session_repository.paths(self.project, "../outside")

    def test_run_manager_repository_injection_uses_one_public_name(self) -> None:
        manager = RunManager(self.sessions, EngineRegistry(), session=self.session, repository=self.runs)
        self.assertIs(manager.repository, self.runs)
        self.assertFalse(hasattr(manager, "runs"))
        with self.assertRaises(TypeError):
            RunManager(self.sessions, EngineRegistry(), session=self.session, runs=self.runs)
