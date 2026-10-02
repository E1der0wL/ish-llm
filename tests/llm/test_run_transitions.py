"""Run 전이 계약과 저장 실패·취소 경계. Engine 효과나 저장 순서를 재구현하지 않는다."""

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus, RunStatus, SessionStatus, validate_run_transition
from llm.engines.base import EngineEvent, EngineEventType
from llm.engines.registry import EngineRegistry
from llm.providers.requests import ProviderError
from llm.services.history.recovery import recover_session
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.lifecycle.sessions import SessionRuntime
from llm.services.runtime.runs import RunManager, RunEventType, _RunOutcome


class TransitionRuleTests(unittest.TestCase):
    def test_only_documented_edges_are_allowed(self):
        allowed = {
            ('start', RunStatus.PENDING, RunStatus.RUNNING),
            ('finish', RunStatus.RUNNING, RunStatus.COMPLETED),
            ('finish', RunStatus.RUNNING, RunStatus.FAILED),
            ('finish', RunStatus.RUNNING, RunStatus.INTERRUPTED),
            ('finish', RunStatus.RUNNING, RunStatus.PAUSED),
            ('recovery', RunStatus.PENDING, RunStatus.INTERRUPTED),
            ('recovery', RunStatus.RUNNING, RunStatus.INTERRUPTED),
        }
        for reason in ('start', 'finish', 'recovery', 'resume'):
            for current in RunStatus:
                for target in RunStatus:
                    with self.subTest(reason=reason, current=current, target=target):
                        if (reason, current, target) in allowed:
                            self.assertIsNone(validate_run_transition(current, target, reason=reason))
                        else:
                            with self.assertRaises(ValueError):
                                validate_run_transition(current, target, reason=reason)

    def test_outcome_is_immutable_and_cannot_mix_success_with_failure(self):
        outcome = _RunOutcome(RunStatus.FAILED, 'failure', 'provider_rate_limit')
        self.assertEqual(outcome.error_code, 'provider_rate_limit')
        with self.assertRaises(FrozenInstanceError):
            outcome.status = RunStatus.COMPLETED
        for status, error, code in ((RunStatus.COMPLETED, 'failure', None),
                                    (RunStatus.PAUSED, None, 'engine_failed'),
                                    (RunStatus.RUNNING, None, None),
                                    (RunStatus.CANCELLED, None, None)):
            with self.subTest(status=status), self.assertRaises(ValueError):
                _RunOutcome(status, error, code)


class RunStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = ProjectManager(ProjectRepository(Path(temporary.name) / 'projects'))
        self.project = self.projects.create('transition')
        self.sessions = self.projects.sessions
        self.session = self.sessions.create(self.project, 'session')
        self.sessions.attach_runtime(self.session)
        self.addCleanup(self.sessions.detach_runtime, self.session)
        self.manager = RunManager(self.sessions, EngineRegistry(), session=self.session)
        self.runtime = SessionRuntime(self.project, self.session)
        self.store = self.manager._store(self.session)

    def begin(self):
        with self.projects.ownership.scope():
            message = self.store.create(MessageRole.USER, 'request', MessageStatus.QUEUED,
                                        metadata={'engine': 'test'})
            return self.manager._begin(self.runtime, message)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in self.project.paths.root.rglob('*') if p.is_file()}

    def test_duplicate_finish_cannot_overwrite_any_domain_record(self):
        for status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED, RunStatus.PAUSED):
            with self.subTest(status=status):
                run = self.begin()
                with self.projects.ownership.scope():
                    self.manager._finish(self.runtime, run, status)
                before, ended = self.snapshot(), run.ended_at
                with self.assertRaisesRegex(ValueError, 'Invalid Run transition'):
                    with self.projects.ownership.scope():
                        self.manager._finish(self.runtime, run, RunStatus.FAILED, 'overwrite')
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(run.ended_at, ended)
                self.assertEqual(run.status, status)

    def test_invalid_finish_is_rejected_before_step_or_message_changes(self):
        run = self.begin()
        for status, error in ((RunStatus.RUNNING, None), (RunStatus.CANCELLED, None),
                              (RunStatus.COMPLETED, 'failure')):
            with self.subTest(status=status):
                before = self.snapshot()
                with patch.object(self.manager.steps, 'list') as steps, self.assertRaises(ValueError):
                    with self.projects.ownership.scope():
                        self.manager._finish(self.runtime, run, status, error)
                steps.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_stale_runtime_run_cannot_clear_the_next_run(self):
        old = self.begin()
        stale = deepcopy(old)
        with self.projects.ownership.scope():
            self.manager._finish(self.runtime, old, RunStatus.COMPLETED)
        current = self.begin()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "current Run"):
            with self.projects.ownership.scope():
                self.manager._finish(self.runtime, stale, RunStatus.FAILED, 'late finish')
        self.assertEqual(self.runtime.session.current_run_id, current.id)
        self.assertEqual(self.snapshot(), before)

    def test_repeated_recovery_preserves_terminal_run_times_and_errors(self):
        completed = self.begin()
        with self.projects.ownership.scope():
            self.manager._finish(self.runtime, completed, RunStatus.COMPLETED)
        original = (completed.paths.root / 'run.json').read_bytes()
        for status in (RunStatus.PENDING, RunStatus.RUNNING):
            with self.subTest(status=status):
                run = self.begin()
                with self.projects.ownership.scope():
                    run.status = status  # process death fixture
                    self.manager.repository.save(run)
                    recovered = recover_session(self.sessions, self.manager.repository, self.manager.steps,
                                                self.session, self.store)
                self.assertEqual([r.id for r in recovered], [run.id])
                self.assertEqual(recovered[0].error_code, 'process_restart')
                persisted = (run.paths.root / 'run.json').read_bytes()
                with self.projects.ownership.scope():
                    self.assertEqual(recover_session(self.sessions, self.manager.repository, self.manager.steps,
                                                     self.session, self.store), [])
                self.assertEqual((run.paths.root / 'run.json').read_bytes(), persisted)
                self.assertEqual((completed.paths.root / 'run.json').read_bytes(), original)


class ProbeEngine:
    def __init__(self, mode='completed'):
        self.mode, self.calls = mode, 0
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def execute(self, context):
        self.calls += 1
        self.entered.set()
        if self.mode == 'blocked':
            await self.release.wait()
        if self.mode == 'failed':
            raise ProviderError('provider_rate_limit')
        if self.mode == 'paused':
            yield EngineEvent(EngineEventType.CHECKPOINT, metadata={
                'name': 'probe', 'operation': 'initialize', 'header': {}, 'records': {}})
            yield EngineEvent(EngineEventType.PAUSED, metadata={'checkpoint': 'probe'})


class RunBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def setup_runtime(self, mode='completed', *, cancel_before_execution=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        projects = ProjectManager(ProjectRepository(Path(temporary.name) / 'projects'))
        project = projects.create('transition')
        session = projects.sessions.create(project, 'session')
        engine, registry, events = ProbeEngine(mode), EngineRegistry(), []
        registry.register('probe', engine)
        def observe(event):
            persisted = manager.repository.load(session, event.run.id)
            # 알림은 Run 저장 성공 이후에만 전달된다.
            self.assertEqual(persisted.status, event.run.status)
            events.append(event)
            if cancel_before_execution and event.type == RunEventType.STARTED:
                manager._active.interrupt_requested = True
        manager = RunManager(projects.sessions, registry, session=session, on_run_event=observe)
        async def cleanup():
            try:
                await manager.shutdown()
            except OSError:
                # 저장 실패 fixture의 worker 오류. 소유권 해제는 shutdown의 finally에서 수행한다.
                if mode != 'blocked':
                    raise
        self.addAsyncCleanup(cleanup)
        return manager, session, engine, events

    async def test_normal_failed_paused_outcomes_match_persisted_notifications(self):
        for mode in ('completed', 'failed', 'paused'):
            with self.subTest(mode=mode):
                manager, session, _, events = await self.setup_runtime(mode)
                await manager.submit('request', engine='probe')
                await asyncio.wait_for(manager.wait_idle(), 10)
                run, = manager.repository.list(session)
                self.assertEqual(run.status, mode)
                self.assertEqual(run.error_code, 'provider_rate_limit' if mode == 'failed' else None)
                self.assertEqual([e.type.value for e in events], ['started', mode])
                self.assertEqual(manager._store(session).get(run.assistant_message_id).status, mode)

    async def test_cancel_before_first_engine_instruction_still_finalizes(self):
        manager, session, engine, events = await self.setup_runtime(cancel_before_execution=True)
        await manager.submit('request', engine='probe')
        await asyncio.wait_for(manager.wait_idle(), 10)
        run, = manager.repository.list(session)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(run.status, RunStatus.INTERRUPTED)
        self.assertEqual(run.error_code, 'interrupted')
        self.assertEqual([e.type.value for e in events], ['started', 'interrupted'])
        self.assertEqual(manager._active.session.status, SessionStatus.IDLE)

    async def test_finish_commit_failure_does_not_publish_terminal_event_or_metric(self):
        manager, session, engine, events = await self.setup_runtime('blocked')
        await manager.submit('request', engine='probe')
        await asyncio.wait_for(engine.entered.wait(), 10)
        with patch.object(manager.sessions, '_save_runtime', side_effect=OSError('disk full')):
            engine.release.set()
            with self.assertRaisesRegex(OSError, 'disk full'):
                await asyncio.wait_for(manager.wait_idle(), 10)
        run, = manager.repository.list(session)
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertIsNone(run.ended_at)
        self.assertEqual(manager._store(session).get(run.assistant_message_id).status, MessageStatus.STREAMING)
        self.assertEqual([e.type.value for e in events], ['started'])
        self.assertEqual(manager.observability.snapshot()['runs']['completed'], 0)
