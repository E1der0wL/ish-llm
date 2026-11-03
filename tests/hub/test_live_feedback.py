"""Responsive steering, bounded Tool evidence and coherent streaming geometry."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock
import tempfile

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.steps import display_steps
from hub.backend.execution.trace import tool_trace
from hub.backend.execution.tool_details import ToolDetails
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.locales.keys import MessageKey
from hub.model import ChatMessage, HubSnapshot, SessionSummary, SessionView, InstructionDisplay
from hub.ui.live import LiveController
from hub.ui.input.submission import SubmittedDraft
from hub.widget.transcript.conversation import ConversationView
from hub.widget.transcript.thought_trace import render_trace_item
from llm.core.execution_observations import ExecutionObservations
from llm.core.results import EngineOutput
from tests.hub.support import SizedOutput


class SteeringFeedbackTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.enterContext(set_app(self.app))
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.controller = LiveController(self.view, HubConfig(root, auto_title=False))
        self.controller.app = self.app
        self.controller._call = Mock()
        self.view.project_id, self.view.connected = 'p', True
        self.messages = (ChatMessage('user', 'Original', id='u'),
                         ChatMessage('assistant', 'Working', id='a', run_id='r', status='streaming'))
        self.view.sessions = [SessionView('Room', id='s', messages=self.messages)]
        self.snapshot = HubSnapshot('p', 'Project', '', 'file',
            (SessionSummary('s', 'Room', 'running'),), 's', self.messages)
        self.view._last_snapshot = self.snapshot

    async def asyncTearDown(self):
        self.view.close()

    def projected(self):
        return self.view.instructions.project(('p', 's'), self.messages)

    async def test_preparing_then_receipt_then_applied_without_extra_bubble(self):
        self.controller.steer('s', 'Read only', expected_run_id='r')
        self.assertEqual(self.projected()[0].instructions[0].status, ChatMessage.PREPARING)
        self.assertEqual(len(self.projected()), 2)
        ready = self.controller._call.call_args.kwargs['completed']
        ready(('r', (('root', 'loop'),)))
        self.assertEqual(self.controller._call.call_args.args[0], HubRuntime.steer.__name__)
        accepted = self.controller._call.call_args.kwargs['completed']
        accepted('receipt')
        self.assertEqual(self.projected()[0].instructions[0].status, 'pending')
        self.assertEqual(self.projected()[0].instructions[0].id, 'receipt')
        applied = replace(self.messages[0], instructions=(InstructionDisplay('receipt', 'Read only', 'applied'),))
        self.view.instructions.observe(replace(self.snapshot, messages=(applied, self.messages[1])))
        self.assertFalse(self.view.instructions.pending)
        self.assertEqual(self.view.instructions.project(('p', 's'), (applied,)), (applied,))

    async def test_failed_submission_restores_draft_and_removes_local_feedback(self):
        self.view.composer.text = 'Read only'
        draft = SubmittedDraft('p', 's', self.view.composer.text)
        draft.consume(self.view)
        self.controller.steer('s', draft.text, expected_run_id='r', draft=draft)
        self.controller._call.call_args.kwargs['completed'](('r', (('root', 'loop'),)))
        self.controller._call.call_args.kwargs['completed'](None)
        self.assertEqual(self.view.composer.text, 'Read only')
        self.assertFalse(self.view.instructions.pending)

    async def test_target_failure_and_cancel_remove_feedback(self):
        self.controller.steer('s', 'Read only', expected_run_id='r')
        self.controller._call.call_args.kwargs['completed'](None)
        self.assertFalse(self.view.instructions.pending)
        self.controller.steer('s', 'Read only', expected_run_id='r')
        self.controller._call.call_args.kwargs['completed'](('r', (('one', 'one'), ('two', 'two'))))
        self.view.close_dialog()
        self.assertFalse(self.view.instructions.pending)

    async def test_receipt_before_callback_and_session_switch_do_not_duplicate(self):
        owner = self.view.instructions
        token = owner.start(('p', 's'), 'Read only', 'r')
        self.assertEqual(owner.project(('p', 'other'), self.messages), self.messages)
        row = replace(self.messages[0], instructions=(InstructionDisplay('receipt', 'Read only', 'applied'),))
        self.view._last_snapshot = replace(self.snapshot, messages=(row, self.messages[1]))
        owner.accepted(token, 'receipt')
        self.assertFalse(owner.pending)


class ToolDetailTests(TestCase):
    def test_generic_file_result_preview_includes_requested_input_and_output(self):
        metadata = {'arguments': {'path': '/tmp/example.py', 'content': 'DO NOT DISPLAY'}}
        ExecutionObservations.store_output(metadata, EngineOutput(data={'text': 'print(1)', 'path': '/tmp/example.py'}))
        details = dict(ToolDetails.project(metadata))
        self.assertIn('/tmp/example.py', details[MessageKey.CARD_INPUT])
        self.assertIn('print(1)', details[MessageKey.TOOL_RESULT])
        self.assertIn('DO NOT DISPLAY', details[MessageKey.CARD_INPUT])

    def test_stored_arguments_results_are_bounded_literal_display_only(self):
        metadata = {'arguments': {'command': 'printf "hello"', 'cwd': '/tmp', 'env': {'SECRET': 'hidden'}}}
        ExecutionObservations.store_output(metadata, EngineOutput(data={
            'stdout': '\x1b[31mhello\n' + 'line\n' * 300000, 'stderr': 'warning', 'returncode': 2}))
        step = NS(name='shell_execute', id='step', status='completed', kind='tool', metadata=metadata)
        summary = display_steps([step])[0]
        text = '\n'.join(value for _, value in summary.details)
        self.assertIn('printf', text)
        self.assertNotIn('/tmp', text)
        self.assertIn('warning', text)
        self.assertNotIn('hidden', text)
        self.assertNotIn('\x1b', text)
        self.assertIn('…', text)
        self.assertLess(len(text), 1500)
        rows = render_trace_item(tool_trace([summary])[0], 80, HubTheme(), Language())
        rendered = '\n'.join(''.join(text for _, text in row) for row in rows)
        self.assertIn('hello', rendered)
        self.assertIn('printf', rendered)
        self.assertEqual(metadata['arguments']['env']['SECRET'], 'hidden')

    def test_process_receipt_exposes_command_and_exit_without_loading_references(self):
        metadata = {'arguments': {'process_id': 'p'}}
        ExecutionObservations.store_output(metadata, EngineOutput(data={
            'argv': ['python', '-c', 'print(1)'], 'returncode': 0, 'stdout': '1',
            'sources': {'stdout': {'path': '/must/not/read'}}}))
        detail = dict(ToolDetails.project(metadata))
        self.assertIn('python', detail[MessageKey.TOOL_RESULT])
        self.assertIn('"returncode": 0', detail[MessageKey.TOOL_RESULT])
        self.assertIn('sources', detail[MessageKey.TOOL_RESULT])


class StreamingGeometryTests(IsolatedAsyncioTestCase):
    async def test_pending_frame_does_not_override_manual_reading_anchor(self):
        view = ConversationView((ChatMessage('assistant', 'line\n\n' * 60, id='a', status='streaming'),), HubTheme())
        control = view.control
        control.create_content(80, 10)
        control.scroll_to(20)
        position = control.reading_position()
        control.render_worker = NS(request=Mock(), cancel=Mock())
        try:
            with set_app(NS(is_running=True, invalidate=Mock())):
                control.messages = (replace(control.messages[0], phase='thinking'),)
                control.create_content(80, 20)
                self.assertFalse(control.follow_tail)
                self.assertEqual(control.reading_position(), position)
        finally:
            control.renderers.close()

    async def test_pending_header_frame_keeps_cursor_scroll_and_height_consistent(self):
        view = ConversationView((ChatMessage('assistant', 'line\n\n' * 60, id='a', status='streaming'),), HubTheme())
        control = view.control
        control.follow_tail = True
        control.create_content(80, 8)
        control.render_worker = NS(request=Mock(), cancel=Mock())
        try:
            with set_app(NS(is_running=True, invalidate=Mock())):
                control.messages = (replace(control.messages[0], phase='thinking'),)
                content = control.create_content(80, 20)
                view.window._scroll_without_linewrapping(content, 80, 20)
                self.assertEqual(control.top_line, view.window.vertical_scroll)
                self.assertEqual(control.top_line, content.line_count - 20)
                self.assertEqual(content.cursor_position.y, content.line_count - 1)
        finally:
            control.renderers.close()

    async def test_urgent_new_header_uses_the_same_extent_as_the_viewport(self):
        view = ConversationView((ChatMessage('user', 'line\n\n' * 60, id='u'),), HubTheme())
        control = view.control
        control.follow_tail = True
        control.create_content(80, 10)
        control.render_worker = NS(request=Mock(), cancel=Mock())
        try:
            with set_app(NS(is_running=True, invalidate=Mock())):
                control.messages = (*control.messages, ChatMessage('assistant', '', id='a', status='preparing'))
                content = control.create_content(80, 10)
                self.assertEqual(content.cursor_position.y, content.line_count - 1)
                self.assertEqual(control.top_line, content.line_count - 10)
        finally:
            control.renderers.close()
