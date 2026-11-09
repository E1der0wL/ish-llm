"""Inspect intermediate PTK frames, not just the final transcript snapshot."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app

from hub.config.theme import HubTheme
from hub.config.view_state import ReadingPosition
from hub.model import ChatMessage, HubSnapshot, SessionSummary, ReasoningMessage
from hub.ui.live import LiveHubView
from hub.widget.transcript.conversation import ConversationView


def lines(content):
    return [''.join(part[1] for part in content.get_line(i))
            for i in range(content.line_count)]


class HeaderHandoffTests(IsolatedAsyncioTestCase):
    def setup_output(self, messages, *, width=80, height=10, follow=True):
        view = ConversationView(messages, HubTheme())
        self.addCleanup(view.control.renderers.close)
        control = view.control
        control.follow_tail = follow
        initial = control.create_content(width, height)
        control.render_worker = NS(request=Mock(), cancel=Mock())
        self.enterContext(set_app(NS(is_running=True, invalidate=Mock())))
        return view, control, initial

    @staticmethod
    def deliver(control, call=None):
        _, build, deliver, _ = (call or control.render_worker.request.call_args).args
        deliver(build())

    async def test_preparing_replacement_keeps_one_header_and_identical_scroll_geometry(self):
        user = ChatMessage('user', 'History\n\n' * 40, id='u')
        local = ChatMessage('assistant', '', id='local', status='preparing', author='Reviewer')
        view, control, initial = self.setup_output((user, local))
        before = (initial.line_count, control.top_line, initial.cursor_position.y)
        row = next(i for i, line in enumerate(lines(initial)) if 'Reviewer' in line)
        control.handoff_message('local', 'answer')
        control.messages = (user, replace(local, id='answer', status='committed'))
        pending = control.create_content(80, 10)
        view.window._scroll_without_linewrapping(pending, 80, 10)
        self.assertEqual([i for i, line in enumerate(lines(pending)) if 'Reviewer' in line], [row])
        self.assertEqual((pending.line_count, control.top_line, pending.cursor_position.y), before)
        self.assertEqual(view.window.vertical_scroll, control.top_line)
        self.deliver(control)
        complete = control.create_content(80, 10)
        self.assertEqual((complete.line_count, control.top_line, complete.cursor_position.y), before)
        self.assertEqual(lines(pending)[row], lines(complete)[row])
        self.assertEqual(control.messages[-1].id, 'answer')
        self.assertFalse(control._header_handoffs)

    async def test_preparing_owner_hands_off_observed_response_and_reasoning(self):
        for response in (
            ChatMessage('assistant', '', id='a', status='committed', author='Reviewer'),
            ChatMessage('assistant', 'Done', id='a', status='completed', author='Reviewer'),
            ReasoningMessage.create('a', 'Thinking', author='Reviewer'),
        ):
            with self.subTest(role=response.role, status=response.status):
                view = LiveHubView()
                try:
                    view.output.transcript.control.render_worker = None
                    base = HubSnapshot('p', 'P', '', 'file', (SessionSummary('s', 'S', 'idle'),), 's', ())
                    view.apply_snapshot(base)
                    view.agent_titles = {'agent': 'Reviewer'}
                    view.preparing.start(('p', 's'), 'Request', recipients=('agent',))
                    view.preparing.accepted(('p', 's'), 'u')
                    user = ChatMessage('user', 'Request', id='u', status='committed')
                    view.apply_snapshot(replace(base, messages=(user,)))
                    control = view.output.transcript.control
                    initial = control.create_content(90, 20)
                    row = next(i for i, line in enumerate(lines(initial)) if 'Reviewer' in line)
                    control.render_worker = NS(request=Mock(), cancel=Mock())
                    view.apply_snapshot(replace(base, messages=(user, response)))
                    with set_app(NS(is_running=True, invalidate=Mock())):
                        pending = control.create_content(90, 20)
                        self.assertEqual([i for i, line in enumerate(lines(pending)) if 'Reviewer' in line], [row])
                        self.deliver(control)
                        final = control.create_content(90, 20)
                        self.assertEqual([i for i, line in enumerate(lines(final)) if 'Reviewer' in line], [row])
                    self.assertFalse(view.preparing.pending)
                finally:
                    view.close()

    async def test_new_empty_header_reserves_the_same_gap_as_its_ready_frame(self):
        user = ChatMessage('user', 'History\n\n' * 40, id='u')
        view, control, _ = self.setup_output((user,))
        control.messages = (user, ChatMessage('assistant', '', id='a', status='preparing'))
        pending = control.create_content(80, 10)
        before = (pending.line_count, control.top_line, pending.cursor_position.y)
        self.deliver(control)
        final = control.create_content(80, 10)
        self.assertEqual((final.line_count, control.top_line, final.cursor_position.y), before)

    async def test_shorter_wrapped_header_clears_old_rows_without_touching_body(self):
        message = ChatMessage('assistant', 'BODY', id='a', status='streaming', author='LONGNAME ' * 16)
        view, control, initial = self.setup_output((message,), width=32, height=20)
        old = lines(initial)
        span = control._header_heights['a']
        self.assertGreater(span, 1)
        control.messages = (replace(message, author='Short'),)
        pending = lines(control.create_content(32, 20))
        self.assertFalse(any('LONGNAME' in line for line in pending))
        self.assertEqual(pending[span:], old[span:])
        self.assertEqual(len(pending), len(old))

    async def test_new_header_before_queued_input_does_not_flash_at_bottom(self):
        user = ChatMessage('user', 'Request', id='u')
        queued = ChatMessage('user', 'Queued', id='q', status='queued')
        view, control, initial = self.setup_output((user, queued))
        control.messages = (user, ChatMessage('assistant', '', id='a', author='Reviewer', status='committed'), queued)
        pending = control.create_content(80, 10)
        self.assertEqual(lines(pending), lines(initial))
        self.deliver(control)
        final = lines(control.create_content(80, 10))
        self.assertLess(next(i for i, line in enumerate(final) if 'Reviewer' in line),
                        next(i for i, line in enumerate(final) if 'Queued' in line))

    async def test_manual_and_restored_reading_positions_follow_explicit_handoff(self):
        for restoring in (False, True):
            with self.subTest(restoring=restoring):
                user = ChatMessage('user', 'History\n\n' * 20, id='u')
                local = ChatMessage('assistant', '', id='local', status='preparing')
                # Room after the response lets manual scroll anchor its header.
                queued = ChatMessage('user', 'Later\n\n' * 20, id='q', status='queued')
                view, control, _ = self.setup_output((user, local, queued), follow=False)
                row = control._anchor_starts['local']
                control.scroll_to(row)
                if restoring:
                    control.restore_position(ReadingPosition('local', 0, row))
                control.handoff_message('local', 'a')
                control.messages = (user, replace(local, id='a', status='committed'), queued)
                control.create_content(80, 10)
                self.assertEqual(control.top_line, row)
                self.deliver(control)
                control.create_content(80, 10)
                self.assertEqual(control.reading_position(), ReadingPosition('a', 0, row))
                self.assertFalse(control.follow_tail)

    async def test_progressive_preparing_frame_cannot_restore_duplicate_header(self):
        user = ChatMessage('user', 'Request', id='u')
        local = ChatMessage('assistant', '', id='local', status='preparing', author='Reviewer')
        view, control, _ = self.setup_output((user, local))
        control.messages = (user, replace(local, elapsed_seconds=1))
        control.create_content(80, 10)
        older = control.render_worker.request.call_args
        control.handoff_message('local', 'a')
        control.messages = (user, replace(local, id='a', status='streaming'))
        control.create_content(80, 10)
        newer = control.render_worker.request.call_args
        self.deliver(control, older)
        pending = lines(control.create_content(80, 10))
        self.assertEqual(sum('Reviewer' in line for line in pending), 1)
        self.assertIn('a', control._header_handoffs)
        self.deliver(control, newer)
        self.assertFalse(control._header_handoffs)

    async def test_session_replacement_clears_handoff(self):
        view, control, _ = self.setup_output((ChatMessage('assistant', '', id='local', status='preparing'),))
        control.handoff_message('local', 'a')
        view.set_messages((ChatMessage('assistant', 'Other session', id='other'),))
        self.assertFalse(control._header_handoffs)
        self.assertFalse(control._header_heights)

    async def test_handoff_survives_old_delivery_before_next_paint_then_releases_on_removal(self):
        user = ChatMessage('user', 'Request', id='u')
        local = ChatMessage('assistant', '', id='local', status='preparing')
        view, control, _ = self.setup_output((user, local))
        control.messages = (user, replace(local, elapsed_seconds=1))
        control.create_content(80, 10)
        older = control.render_worker.request.call_args
        control.handoff_message('local', 'a')
        control.messages = (user, replace(local, id='a', status='committed'))
        # An old worker callback can run before the invalidated UI is repainted.
        self.deliver(control, older)
        self.assertIn('a', control._header_handoffs)
        control.messages = (user,)
        control.create_content(80, 10)
        self.deliver(control)
        self.assertFalse(control._header_handoffs)

    async def test_multiple_agents_replace_only_the_identified_preparation_header(self):
        user = ChatMessage('user', 'Request', id='u')
        local = ChatMessage('assistant', '', id='local', status='preparing', author='Local feedback')
        view, control, initial = self.setup_output((user, local))
        old_row = next(i for i, line in enumerate(lines(initial)) if 'Local feedback' in line)
        control.handoff_message('local', 'a')
        control.messages = (user,
            ChatMessage('assistant', '', id='a', status='committed', author='Reviewer'),
            ChatMessage('assistant', '', id='b', status='streaming', author='Writer'))
        pending = control.create_content(80, 10)
        painted = lines(pending)
        self.assertFalse(any('Local feedback' in line for line in painted))
        self.assertEqual([i for i, line in enumerate(painted) if 'Reviewer' in line], [old_row])
        self.assertEqual(sum('Writer' in line for line in painted), 1)
        self.deliver(control)
        final = control.create_content(80, 10)
        self.assertEqual(final.line_count, pending.line_count)
        self.assertEqual(lines(final), painted)
