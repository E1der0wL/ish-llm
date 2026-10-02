from llm.core.results import EngineDelta
from asyncio import timeout
import asyncio
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from llm.core.models import (
    MessageRole, MessageStatus, ProjectConfig, Run, RunStatus, StepStatus, SessionStatus, new_id,
)
from llm.engines.base import EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from tests.llm.support.fake_engine import FakeStreamingEngine
from llm.services.history.conversation import ConversationStore
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.lifecycle.sessions import SessionManager


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sessions = SessionManager()
        self.projects = ProjectManager(ProjectRepository(self.root / "projects"), self.sessions)
        self.project = self.projects.create("Project", config=ProjectConfig())
        self.session = self.sessions.create(self.project, "Session")
        self.store = ConversationStore(self.session.paths.conversation)
        self.engine = FakeStreamingEngine()
        self.registry = EngineRegistry()
        self.registry.register("fake", self.engine)
        self.manager = self.make_manager()

    def make_manager(self, session=None) -> RunManager:
        manager = RunManager(self.sessions, self.registry, session=session or self.session)
        self.addAsyncCleanup(manager.shutdown)
        return manager

    async def idle(self, session=None, manager=None) -> None:
        await asyncio.wait_for((manager or self.manager).wait_idle(
            ), timeout=10)

    async def until(self, predicate) -> None:
        async with timeout(10):
            while not predicate():
                await asyncio.sleep(0.001)

    async def test_single_request_is_durable_before_execution(self) -> None:
        request = await self.manager.submit("hello", engine="fake")
        self.assertEqual(self.store.get(request.id).status, MessageStatus.QUEUED)
        self.assertEqual(self.manager.repository.list(self.session), [])
        await self.idle()
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        self.assertEqual(run.input_message_id, request.id)
        self.assertEqual(self.store.get(request.id).run_id, run.id)
        assistant = self.store.get(run.assistant_message_id)
        self.assertEqual(assistant.content, "Hello world")
        self.assertEqual(assistant.status, MessageStatus.COMPLETED)
        step, = self.manager.steps.list(run)
        self.assertEqual(step.status, StepStatus.COMPLETED)
        self.assertIsNotNone(step.started_at)
        self.assertIsNotNone(step.ended_at)
        loaded = self.sessions.load(self.project, self.session.id)
        self.assertEqual(loaded.status, SessionStatus.IDLE)
        self.assertIsNone(loaded.current_run_id)

    async def test_streaming_and_second_input_remains_queued(self) -> None:
        self.engine.gate = asyncio.Event()
        await self.manager.submit("first", engine="fake")
        await self.until(lambda: any(message.content == "Hello" for message in self.store.list()))
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(self.store.get(run.assistant_message_id).status, MessageStatus.STREAMING)
        self.assertEqual(self.manager.steps.list(run)[0].status, StepStatus.RUNNING)
        second = await self.manager.submit("second", engine="fake")
        prefix = self.store.path.read_bytes()
        self.assertEqual(self.store.get(second.id).status, MessageStatus.QUEUED)
        self.assertEqual(len(self.engine.contexts), 1)
        self.assertEqual([message.content for message in self.engine.contexts[0].messages], ["first"])
        self.engine.gate.set()
        await self.idle()
        self.assertTrue(self.store.path.read_bytes().startswith(prefix))
        self.assertEqual([message.content for message in self.engine.contexts[1].messages],
                         ["first", "Hello world", "second"])
        self.assertEqual(self.engine.max_active, 1)

    async def test_burst_queue_context_pairs_answers_with_prior_inputs(self) -> None:
        for content in ("first", "second", "third"):
            await self.manager.submit(content, engine="fake")
        await self.idle()
        self.assertEqual([[message.content for message in context.messages]
                          for context in self.engine.contexts], [
            ["first"], ["first", "Hello world", "second"],
            ["first", "Hello world", "second", "Hello world", "third"],
        ])
        self.assertEqual(self.engine.max_active, 1)
        self.assertEqual(len(self.manager.repository.list(self.session)), 3)

    async def test_interrupt_preserves_queue_and_worker_continues(self) -> None:
        self.engine.gate = asyncio.Event()
        await self.manager.submit("first", engine="fake")
        await self.until(lambda: self.engine.active == 1)
        second = await self.manager.submit("second", engine="fake")
        third = await self.manager.submit("third", engine="fake")
        self.assertTrue(await self.manager.interrupt())
        first_run = self.manager.repository.list(self.session)[0]
        self.assertEqual(first_run.status, RunStatus.INTERRUPTED)
        self.assertEqual(self.store.get(first_run.assistant_message_id).content, "Hello")
        self.assertEqual(self.store.get(first_run.assistant_message_id).status, MessageStatus.INTERRUPTED)
        self.assertEqual(self.manager.steps.list(first_run)[0].status, StepStatus.INTERRUPTED)
        self.assertEqual(self.store.get(third.id).status, MessageStatus.QUEUED)
        self.engine.gate.set()
        await self.idle()
        self.assertEqual(self.store.get(second.id).status, MessageStatus.COMMITTED)
        self.assertEqual([run.status for run in self.manager.repository.list(self.session)],
                         [RunStatus.INTERRUPTED, RunStatus.COMPLETED, RunStatus.COMPLETED])
        self.assertEqual(self.engine.cancelled, 1)
        self.assertFalse(await self.manager.interrupt())

    async def test_cancel_before_engine_first_instruction_finalizes_run(self) -> None:
        await self.manager.submit("first", engine="fake")
        # The worker begins and schedules its child behind this test continuation.
        await asyncio.sleep(0)
        self.assertEqual(len(self.engine.contexts), 0)
        self.assertTrue(await self.manager.interrupt())
        await self.idle()
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.INTERRUPTED)
        self.assertEqual(self.store.get(run.assistant_message_id).status, MessageStatus.INTERRUPTED)
        self.assertIsNone(self.sessions.load(self.project, self.session.id).current_run_id)

    async def test_independent_sessions_really_run_concurrently(self) -> None:
        self.engine.gate = asyncio.Event()
        other = self.sessions.create(self.project, "Other")
        other_manager = self.make_manager(other)
        await self.manager.submit("first", engine="fake")
        await other_manager.submit("other", engine="fake")
        await self.until(lambda: self.engine.active == 2)
        self.assertEqual(self.engine.max_active, 2)
        self.assertTrue(await self.manager.interrupt())
        other_run, = self.manager.repository.list(other)
        self.assertEqual(other_run.status, RunStatus.RUNNING)
        self.engine.gate.set()
        await asyncio.gather(self.idle(), self.idle(manager=other_manager))
        self.assertEqual(self.manager.repository.list(other)[0].status, RunStatus.COMPLETED)

    async def test_engine_failure_preserves_partial_text_and_processes_next_request(self) -> None:
        self.engine.fail_after = 1
        self.engine.fail_inputs = frozenset({"fail"})
        await self.manager.submit("fail", engine="fake")
        await self.manager.submit("success", engine="fake")
        await self.idle()
        failed, completed = self.manager.repository.list(self.session)
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertEqual(self.store.get(failed.assistant_message_id).content, "Hello")
        self.assertEqual(self.store.get(failed.assistant_message_id).status, MessageStatus.FAILED)
        self.assertEqual(self.manager.steps.list(failed)[0].status, StepStatus.FAILED)
        self.assertEqual(self.engine.active, 0)

    async def test_unknown_engine_fails_one_run_without_dropping_queue(self) -> None:
        with self.assertRaises(ValueError):
            await self.manager.submit("bad", engine="missing")
        await self.manager.submit("good", engine="fake")
        await self.idle()
        self.assertEqual([run.status for run in self.manager.repository.list(self.session)],
                         [RunStatus.COMPLETED])

    async def test_explicit_engine_choices_are_durable(self) -> None:
        alternate = FakeStreamingEngine(("alternate",))
        self.registry.register("alternate", alternate)
        await self.manager.submit("selected", engine="alternate")
        await self.manager.submit("override", engine="fake")
        await self.idle()
        self.assertEqual([run.engine for run in self.manager.repository.list(self.session)], ["alternate", "fake"])
        self.assertEqual(len(alternate.contexts), 1)

    async def test_shutdown_preserves_queue_for_new_manager(self) -> None:
        self.engine.gate = asyncio.Event()
        await self.manager.submit("active", engine="fake")
        await self.until(lambda: self.engine.active == 1)
        queued = await self.manager.submit("queued", engine="fake")
        await self.manager.shutdown()
        self.assertEqual(self.engine.active, 0)
        self.assertEqual(self.store.get(queued.id).status, MessageStatus.QUEUED)
        self.assertEqual(self.manager.repository.list(self.session)[0].status, RunStatus.INTERRUPTED)
        before = self.store.path.read_bytes()
        with self.assertRaises(RuntimeError):
            await self.manager.submit("rejected", engine="fake")
        self.assertEqual(self.store.path.read_bytes(), before)
        self.engine.gate.set()
        recovered = self.make_manager()
        await recovered.start()
        await self.idle(manager=recovered)
        self.assertEqual([context.messages[-1].content for context in self.engine.contexts], ["active", "queued"])

    async def test_shutdown_before_worker_starts_keeps_all_requests_queued(self) -> None:
        await self.manager.submit("queued", engine="fake")
        await self.manager.shutdown()
        self.assertEqual(self.manager.repository.list(self.session), [])
        self.assertEqual(self.store.list()[0].status, MessageStatus.QUEUED)
        recovered = self.make_manager()
        await recovered.start()
        await self.idle(manager=recovered)
        self.assertEqual(len(self.engine.contexts), 1)

    async def test_recovery_interrupts_stale_runs_steps_and_orphan_streams(self) -> None:
        stale_runs = []
        for status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.COMPLETED):
            message = self.store.create(MessageRole.USER, f"old-{status}", MessageStatus.COMMITTED)
            run_id = new_id()
            assistant = self.store.create(MessageRole.ASSISTANT, "partial", MessageStatus.STREAMING,
                                          run_id=run_id)
            run = Run(run_id, self.session.id, message.id, assistant.id, "fake",
                      self.manager.repository.paths(self.session, run_id), status=status)
            self.manager.repository.save(run)
            self.store.bind_run(message.id, run.id)
            self.manager.steps.create(run, "tool", "pending")
            step = self.manager.steps.create(run, "llm", "running")
            self.manager.steps.start(step)
            stale_runs.append(run)
        orphan = self.store.create(MessageRole.ASSISTANT, "orphan", MessageStatus.STREAMING)
        self.session.status = SessionStatus.RUNNING
        self.session.current_run_id = stale_runs[1].id
        self.sessions.repository.save(self.session)
        queued = self.store.create(MessageRole.USER, "recover-me", MessageStatus.QUEUED, metadata={"engine": "fake"})
        await self.manager.start()
        await self.manager.start()
        await self.idle()
        self.assertEqual(len(self.engine.contexts), 1)
        self.assertEqual(self.engine.contexts[0].messages[-1].id, queued.id)
        for run in stale_runs:
            expected = RunStatus.COMPLETED if run.status == RunStatus.COMPLETED else RunStatus.INTERRUPTED
            self.assertEqual(self.manager.repository.load(self.session, run.id).status, expected)
            self.assertTrue(all(step.status == StepStatus.INTERRUPTED
                                for step in self.manager.steps.list(run)))
            self.assertEqual(self.store.get(run.assistant_message_id).status, MessageStatus.INTERRUPTED)
        self.assertEqual(self.store.get(orphan.id).status, MessageStatus.INTERRUPTED)

    async def test_crash_between_run_creation_and_commit_does_not_replay_claimed_input(self) -> None:
        claimed = self.store.create(MessageRole.USER, "claimed", MessageStatus.QUEUED)
        run_id = new_id()
        run = Run(run_id, self.session.id, claimed.id, new_id(), "fake",
                  self.manager.repository.paths(self.session, run_id))
        self.manager.repository.save(run)
        self.store.create(MessageRole.USER, "future", MessageStatus.QUEUED, metadata={"engine": "fake"})
        await self.manager.start()
        await self.idle()
        self.assertEqual([context.messages[-1].content for context in self.engine.contexts], ["future"])
        self.assertEqual(self.store.get(claimed.id).status, MessageStatus.COMMITTED)
        self.assertEqual(self.store.get(claimed.id).run_id, run.id)
        self.assertEqual(self.manager.repository.load(self.session, run.id).status, RunStatus.INTERRUPTED)
        await self.manager.shutdown()
        recovered = self.make_manager()
        await recovered.start()
        await self.idle(manager=recovered)
        self.assertEqual(len(self.engine.contexts), 1)

    async def test_real_process_restart_preserves_partial_output_and_queued_input(self) -> None:
        code = textwrap.dedent('''
            import asyncio, os, sys
            from pathlib import Path
            from llm.engines.registry import EngineRegistry
            from tests.llm.support.fake_engine import FakeStreamingEngine
            from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
            from llm.services.lifecycle.sessions import SessionManager
            from llm.services.runtime.runs import RunManager
            async def main():
                sessions = SessionManager()
                projects = ProjectManager(ProjectRepository(Path(sys.argv[1])), sessions)
                project = projects.load(sys.argv[2])
                session = sessions.load(project, sys.argv[3])
                engine = FakeStreamingEngine(gate=asyncio.Event())
                registry = EngineRegistry()
                registry.register("fake", engine)
                manager = RunManager(sessions, registry, session=session)
                await manager.submit("crashed", engine="fake")
                while not engine.active:
                    await asyncio.sleep(0)
                await manager.submit("survived", engine="fake")
                os._exit(23)
            asyncio.run(main())
        ''')
        result = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", code, str(self.root / "projects"),
                             self.project.id, self.session.id],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 23, result.stderr)
        stale, = self.manager.repository.list(self.session)
        self.assertEqual(stale.status, RunStatus.RUNNING)
        self.assertEqual(self.store.get(stale.assistant_message_id).content, "Hello")
        await self.manager.start()
        await self.idle()
        self.assertEqual([context.messages[-1].content for context in self.engine.contexts], ["survived"])
        self.assertEqual(self.manager.repository.load(self.session, stale.id).status, RunStatus.INTERRUPTED)
        self.assertEqual(self.manager.steps.list(stale)[0].status, StepStatus.INTERRUPTED)
        self.assertEqual(self.store.get(stale.assistant_message_id).status, MessageStatus.INTERRUPTED)

    async def test_provider_error_diagnostics_are_recorded(self) -> None:
        class FailingEngine:
            async def execute(self, context):
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=new_id())
                raise RuntimeError("provider failure detail")
        self.registry.register("provider-error", FailingEngine())
        await self.manager.submit("request", engine="provider-error")
        await self.idle()
        self.assertEqual(self.manager.repository.list(self.session)[0].error, "provider failure detail")
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(self.manager.steps.list(run)[0].status, StepStatus.FAILED)

    async def test_unfinished_step_fails_run_and_closes_generator(self) -> None:
        closed = asyncio.Event()
        class IncompleteEngine:
            async def execute(self, context):
                try:
                    yield EngineEvent(EngineEventType.STEP_STARTED, step_id=new_id())
                    yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("test-output", "partial"))
                finally:
                    closed.set()
        self.registry.register("incomplete", IncompleteEngine())
        await self.manager.submit("request", engine="incomplete")
        await self.idle()
        self.assertTrue(closed.is_set())
        run, = self.manager.repository.list(self.session)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertEqual(self.manager.steps.list(run)[0].status, StepStatus.FAILED)

    async def test_engine_receives_snapshot_not_mutable_service_state(self) -> None:
        class MutatingEngine:
            async def execute(self, context):
                context.project.title = "mutated"
                context.session.title = "mutated"
                context.run.engine = "mutated"
                context.messages[-1].content = "mutated"
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("test-output", "answer"))
        self.registry.register("mutating", MutatingEngine())
        request = await self.manager.submit("original", engine="mutating")
        await self.idle()
        self.assertEqual(self.store.get(request.id).content, "original")
        self.assertEqual(self.sessions.load(self.project, self.session.id).title, "Session")
        self.assertEqual(self.manager.repository.list(self.session)[0].engine, "mutating")

    async def test_deleted_session_and_wrong_project_are_rejected(self) -> None:
        self.sessions.delete(self.session)
        with self.assertRaises(ValueError):
            await self.manager.submit("rejected", engine="fake")
        self.assertEqual(self.store.list(), [])
        self.sessions.restore(self.session)
        other = self.projects.create("Other")
        from copy import deepcopy
        wrong = deepcopy(self.session)
        wrong.project_id = other.id
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.make_manager(wrong).submit("rejected", engine="fake")

    async def test_empty_response_and_failure_before_first_delta(self) -> None:
        self.registry.register("empty", FakeStreamingEngine(()))
        self.registry.register("early-failure", FakeStreamingEngine(fail_after=0))
        await self.manager.submit("empty", engine="empty")
        await self.manager.submit("fail", engine="early-failure")
        await self.idle()
        completed, failed = self.manager.repository.list(self.session)
        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertEqual(failed.status, RunStatus.FAILED)
        self.assertEqual(self.store.get(completed.assistant_message_id).content, "")
        self.assertEqual(self.store.get(failed.assistant_message_id).content, "")

    async def test_plain_async_iterator_engine_is_supported(self) -> None:
        class Events:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class IteratorEngine:
            def execute(self, context):
                return Events()

        self.registry.register("iterator", IteratorEngine())
        await self.manager.submit("request", engine="iterator")
        await self.idle()
        self.assertEqual(self.manager.repository.list(self.session)[0].status, RunStatus.COMPLETED)

    async def test_cloned_burst_history_preserves_turn_order(self) -> None:
        await self.manager.submit("first", engine="fake")
        await self.manager.submit("second", engine="fake")
        await self.idle()
        await self.manager.shutdown()
        clone = self.sessions.clone(self.sessions.load(self.project, self.session.id), self.project)
        recovered = self.make_manager(clone)
        await recovered.submit("third", engine="fake")
        await self.idle(session=clone, manager=recovered)
        self.assertEqual([message.content for message in self.engine.contexts[-1].messages],
                         ["first", "Hello world", "second", "Hello world", "third"])

    async def test_wait_idle_during_shutdown_does_not_hang_on_preserved_queue(self) -> None:
        self.engine.gate = asyncio.Event()
        await self.manager.submit("active", engine="fake")
        await self.until(lambda: self.engine.active == 1)
        await self.manager.submit("queued", engine="fake")
        waiting = asyncio.create_task(self.manager.wait_idle())
        await asyncio.sleep(0)
        await self.manager.shutdown()
        with self.assertRaisesRegex(RuntimeError, "stopped"):
            await asyncio.wait_for(waiting, timeout=1)
