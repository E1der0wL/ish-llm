"""Hub displays handled failures without changing native execution decisions."""

from types import SimpleNamespace
import unittest

from hub.backend.execution.steps import display_steps
from hub.backend.execution.thinking import ThinkingPhases
from hub.backend.observability.notifications import NotificationBuffer
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import TraceItem
from hub.widget.transcript.thought_trace import render_trace_item
from llm.core.step_failures import FailureHandling
from llm.engines.events import EngineEvent, EngineEventType
from llm.components.memory.processing import MemorySession


class MemoryFailureDisplayTests(unittest.TestCase):
    def test_handled_failure_is_failed_and_run_notification_uses_run_status(self):
        metadata = {FailureHandling.METADATA: FailureHandling('loop', 'handled/s', 'a' * 64).to_dict()}
        step = SimpleNamespace(id='s', name=MemorySession.ACTIVE_STEP, kind='memory',
                               status='failed', started_at='2026-11-04T00:00:00Z', metadata=metadata)
        shown, = display_steps([step])
        self.assertEqual(shown.status, 'failed')
        self.assertTrue(shown.handled_failure)
        item = TraceItem(shown.id, TraceItem.SUMMARY, '', shown.status,
                         handled_failure=shown.handled_failure)
        text = ''.join(fragment[1] for row in render_trace_item(item, 100, HubTheme(), Language('ko'))
                       for fragment in row)
        self.assertIn('실패', text)
        self.assertIn('작업 계속', text)
        notices = NotificationBuffer()
        notices.register_session('p', 'session', 'Example')
        run = SimpleNamespace(id='r', session_id='session', engine='loop', status='completed')
        notices.record_run(run)
        self.assertEqual(notices.items()[-1].status, 'completed')
        self.assertEqual(step.status, 'failed')

    def test_failed_summary_does_not_remain_summarizing(self):
        phases = ThinkingPhases()
        run = SimpleNamespace(id='r', status='running', metadata={'completions': []})
        phases.observe(run, EngineEvent(EngineEventType.STEP_STARTED, step_id='s',
                                       name=MemorySession.ACTIVE_STEP, kind='memory'))
        phases.observe(run, EngineEvent(EngineEventType.STEP_FAILED, step_id='s'))
        self.assertNotEqual(phases.activity(run), 'summarizing')
        shown, = display_steps([SimpleNamespace(id='s', name=MemorySession.ACTIVE_STEP,
            kind='memory', status='failed', metadata={})])
        self.assertFalse(shown.handled_failure)
