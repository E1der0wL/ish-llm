"""Persisted admission feedback does not wait for expensive history projection."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from hub.backend.runtime import HubRuntime
from hub.backend.execution.request_profile import RequestMode, RequestProfile
from hub.model import ChatMessage, HubSnapshot, RequestStarted, SessionSummary
from hub.ui.live import LiveHubView
from hub.ui.submitted import SubmittedMessages
from llm.core.models import MessageStatus, RunStatus, Run
from llm.services.runtime.runs import RunEvent, RunEventType


class SubmissionObservationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.view = LiveHubView()
        self.message = ChatMessage('user', 'hello', id='request', status='queued')
        self.snapshot = HubSnapshot(
            project_id='project', project_title='Project', model='model', storage='file',
            sessions=(SessionSummary('session', 'Session', 'idle'),), selected_id='session',
            messages=(self.message,),
        )
        self.view.apply_snapshot(self.snapshot)
        self.notice = RequestStarted('project', 'session', 'request', 'run')

    def tearDown(self):
        self.view.close()
        self.view.progress.close()

    def test_start_updates_visible_row_without_a_history_read(self):
        self.view.show_request_started(self.notice)
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.COMMITTED)
        self.assertEqual(self.view.output.transcript.control.messages[0].status, MessageStatus.COMMITTED)
        self.assertIsNone(HubSnapshot.last_observed(self.view).run)
        self.view.apply_snapshot(self.snapshot)  # A previously started read returns late.
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.COMMITTED)
        completed = replace(self.message, status=MessageStatus.COMPLETED)
        self.view.apply_snapshot(replace(self.snapshot, messages=(completed,)))
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.COMPLETED)
        self.assertFalse(getattr(self.view, SubmittedMessages.STARTED_FIELD))

    def test_start_before_submit_ack_does_not_restore_queued(self):
        self.view.apply_snapshot(replace(self.snapshot, messages=()))
        self.view.show_request_started(self.notice)
        self.view.show_submitted('project', 'session', self.message)
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.COMMITTED)
        self.view.apply_snapshot(replace(self.snapshot, messages=()))
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.COMMITTED)

    def test_scope_and_terminal_rows_are_preserved(self):
        self.view.show_request_started(replace(self.notice, project_id='another'))
        self.view.show_request_started(replace(self.notice, session_id='another'))
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.QUEUED)
        terminal = replace(self.message, status=MessageStatus.CANCELLED)
        self.view.apply_snapshot(replace(self.snapshot, messages=(terminal,)))
        self.view.show_request_started(self.notice)
        self.assertEqual(self.view.sessions[0].messages[0].status, MessageStatus.CANCELLED)

    async def test_runtime_start_signal_does_not_acquire_snapshot_lock(self):
        runtime = HubRuntime.__new__(HubRuntime)
        runtime.project = NS(id='project')
        runtime.sessions = {'session': NS(project=runtime.project)}
        runtime.background_records = Mock()
        runtime.automatic_approvals = Mock()
        runtime.snapshot_reader = Mock()
        runtime.notifications = Mock()
        runtime.drafts = Mock()
        runtime.dirty = asyncio.Event()
        runtime.lock = asyncio.Lock()
        runtime.request_started = Mock()
        run = Run('run', 'session', 'request', 'answer', 'loop', None,
                  status=RunStatus.RUNNING, agent_id='assistant')
        async with runtime.lock:
            async with asyncio.timeout(1):
                await runtime._run_event(NS(run=run))
        runtime.request_started.assert_called_once_with(self.notice)
        runtime.snapshot_reader.read.assert_not_called()
        runtime.request_started.reset_mock()
        run.status = RunStatus.COMPLETED
        await runtime._run_event(NS(run=run))
        runtime.request_started.assert_not_called()

    def test_idle_reconciliation_preserves_cached_message_tuple(self):
        self.assertIs(SubmittedMessages.reconcile(self.view, self.snapshot), self.snapshot)
        other = replace(self.notice, session_id='another')
        SubmittedMessages.started(self.view, other)
        self.assertIs(SubmittedMessages.reconcile(self.view, self.snapshot), self.snapshot)

    async def test_idle_preflight_uses_fresh_session_without_full_runtime_query(self):
        runtime = HubRuntime.__new__(HubRuntime)
        session = NS(aget_data=AsyncMock(return_value=NS(active_run_ids=[])),
                     run=NS(astatus=AsyncMock()))
        runtime.sessions = {'session': session}
        result = await runtime.submission_state('session', 'agent')
        self.assertIsNone(result['run_id'])
        session.aget_data.assert_awaited_once()
        session.run.astatus.assert_not_awaited()

    async def test_native_started_event_uses_session_owner_without_reading_history(self):
        runtime = HubRuntime.__new__(HubRuntime)
        # A real Run deliberately has no project_id. Its Session owns the
        # Project identity, independently of the UI's currently selected page.
        run = Run('run', 'session', 'request', 'answer', 'loop', None,
                  status=RunStatus.RUNNING, agent_id='assistant')
        runtime.project = NS(id='another-project')
        runtime.sessions = {'session': NS(project=NS(id='project'),
                                          aget_data=AsyncMock())}
        runtime.request_started = Mock()
        runtime.background_records = NS(observe=Mock())
        runtime.automatic_approvals = NS(observe=Mock())
        runtime.snapshot_reader = NS(observe_run=Mock(), drafts=NS(forget=Mock()))
        runtime.notifications = NS(record_run=Mock())
        runtime.urgent_observation = False
        runtime.dirty = asyncio.Event()
        await runtime._run_event(RunEvent(RunEventType.STARTED, run))
        runtime.request_started.assert_called_once_with(self.notice)
        runtime.sessions['session'].aget_data.assert_not_awaited()
        runtime.snapshot_reader.observe_run.assert_called_once_with(run)
        runtime.notifications.record_run.assert_called_once_with(run)
        self.assertTrue(runtime.urgent_observation)
        self.assertTrue(runtime.dirty.is_set())

        runtime.request_started.reset_mock()
        runtime.sessions.clear()  # A late event from the previously selected Project.
        await runtime._run_event(RunEvent(RunEventType.STARTED, run))
        runtime.request_started.assert_not_called()

    async def test_active_preflight_keeps_authoritative_recipient_lookup(self):
        runtime = HubRuntime.__new__(HubRuntime)
        session = NS(aget_data=AsyncMock(return_value=NS(active_run_ids=['a','b'])),
                     run=NS(astatus=AsyncMock(return_value=NS(active_runs=[
                         NS(run_id='a', agent_id='first'), NS(run_id='b', agent_id='second')]))))
        runtime.sessions = {'session': session}
        self.assertEqual((await runtime.submission_state('session', 'second'))['run_id'], 'b')
        session.run.astatus.assert_awaited_once_with(queued_limit=0)

    def test_explanation_topics_do_not_select_work_but_mixed_actions_do(self):
        for text in ('마크다운으로 README 파일 작성하는 예시 보여줘',
                     '파일 삭제 방법을 설명해줘', 'show how to write a README'):
            self.assertEqual(RequestProfile.classify(text).mode, RequestMode.LOOKUP, text)
        for text in ('README 파일 작성해줘', '파일 삭제 방법 알려주고 실행해줘',
                     'README 작성 예시를 보여주고 파일도 만들어줘',
                     'show how to write a README and save it'):
            self.assertEqual(RequestProfile.classify(text).mode, RequestMode.WORK, text)
