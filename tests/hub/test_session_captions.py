"""Home uses one observed attention state; its footer keeps actionable warnings."""

from dataclasses import replace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput

from examples.hub.preview import create_preview
from hub.errors.contracts import ErrorInfo
from hub.locales import Language
from hub.model import HubSnapshot, RunSummary, SessionSummary, SessionView
from hub.ui.presentation import present
from tests.hub.support import SizedOutput


class SessionCaptionTests(IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict('os.environ', {'LC_ALL': 'ko_KR.UTF-8', 'LANGUAGE': 'ko'}):
            self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.addCleanup(self.view.close)
        self.view.project_id = 'p'
        self.view.sessions = [SessionView('First', 'stale caption', id='s'),
                              SessionView('Second', 'stale caption', id='b')]
        self.view.selected = 0
        self.control = self.view._session_control
        self.snapshot = HubSnapshot('p', 'Project', 'demo/model', 'file', (
            SessionSummary('s', 'First', 'idle'),
            SessionSummary('b', 'Second', 'running', queued_count=2, active=True),
        ), 's', ())

    def test_sidebar_and_header_share_state_queue_and_selection(self):
        self.view.visible = True
        self.control.observe(self.snapshot)
        with set_app(self.app):
            self.assertEqual(self.control.rows()[0].detail, self.view.icons.session_idle + ' 유휴 • 예약 0')
            self.assertEqual(self.view.output.heading(), 'First • ' + self.control.rows()[0].detail)
            self.view.selected = 1
            with patch('hub.widget.session_list.monotonic', return_value=10):
                with patch.object(self.control.spinner, 'render', wraps=self.control.spinner.render) as frame:
                    rows = self.control.rows()
                    self.assertEqual(self.view.output.heading(), 'Second • ' + rows[1].detail)
                    self.assertIs(self.control.rows(), rows)
                    self.assertEqual(frame.call_count, 1)
                    self.assertIn('실행 중 • 예약 2', rows[1].detail)
            updated = replace(self.snapshot.sessions[1], pending_input=True, queued_count=1)
            self.control.observe(replace(self.snapshot, sessions=(self.snapshot.sessions[0], updated)))
            self.assertEqual(self.view.output.heading(),
                             'Second • ' + self.view.icons.question + ' 입력필요 • 예약 1')
            self.view.t = Language('en', icons=lambda: self.view.icons)
            self.assertEqual(self.view.output.heading(),
                             'Second • ' + self.view.icons.question + ' Input required • queued 1')

    def test_completion_acknowledgement_and_empty_home_do_not_leave_stale_header(self):
        done = replace(self.snapshot.sessions[0], latest_run_id='done', latest_run_status='completed')
        self.control.observe(replace(self.snapshot, sessions=(done,)))
        with set_app(self.app):
            self.assertIn('완료 • 예약 0', self.view.output.heading())
            self.view.visible = True
            self.assertIn('유휴 • 예약 0', self.view.output.heading())
            self.assertEqual(done.latest_run_status, 'completed')
            self.view.no_sessions = True
            self.assertEqual(self.view.output.heading(), '')
            self.assertEqual(self.view.output.header.preferred_height(100, 30).preferred, 1)

    async def test_visible_header_animates_without_visible_sidebar_and_stops_when_hidden(self):
        self.view.visible = True
        self.view.selected = 1
        self.control.observe(self.snapshot)
        with set_app(self.app):
            self.view.output.heading()
            with patch.object(self.app.layout, 'visible_windows', [self.view.output.header]):
                self.control._after_render(self.app)
                timer = self.control._timer
                self.assertIsNotNone(timer)
                self.control._after_render(self.app)
                self.assertIs(timer, self.control._timer)
                self.view.settings_open = True
                self.control._after_render(self.app)
                self.assertTrue(timer.cancelled())
                self.assertIsNone(self.control._timer)
                self.view.settings_open = False
                self.view.output.heading()
                self.control._after_render(self.app)
                timer = self.control._timer
                self.assertIsNotNone(timer)
            with patch.object(self.app.layout, 'visible_windows', []):
                self.control._after_render(self.app)
                self.assertTrue(timer.cancelled())
                self.assertIsNone(self.control._timer)

    def test_footer_omits_routine_states_but_preserves_error_and_setup_guidance(self):
        language = self.view.t
        for state in ('idle', 'running', 'paused', 'waiting_input', 'waiting_approval'):
            with self.subTest(state=state):
                snapshot = replace(self.snapshot, status=state, queued_count=2, storage='memory',
                    run=RunSummary('r', state, 'loop', 3, state == 'running', (), ''))
                self.assertEqual(present(snapshot, language).notice, '')
        failed = replace(self.snapshot, status='waiting_input',
            run=RunSummary('r', 'failed', 'loop', 3, False, (), 'Read permission denied'))
        self.assertIn('Read permission denied', present(failed, language).notice)
        self.assertEqual(present(replace(self.snapshot, model_required=True), language).notice,
                         language('model_required'))
        title = ErrorInfo(code='title_failure', message='Title unavailable')
        self.assertIn('Title unavailable', present(replace(self.snapshot, title_error=title), language).notice)
        self.assertEqual(present(replace(self.snapshot, sessions=()), language).notice,
                         language('session_required'))

    def test_progress_still_occupies_the_shared_footer_then_returns_to_warning(self):
        self.view.visible = True
        self.view.notice = present(replace(self.snapshot, model_required=True), self.view.t).notice
        with set_app(self.app):
            control = self.view.progress.container(lambda: self.view.notice).content
            def text():
                content = control.create_content(100, 1)
                self.assertEqual(content.line_count, 1)
                return ''.join(fragment[1] for fragment in content.get_line(0))
            warning = text()
            self.assertIn('/agent', warning)
            task = self.view.progress.start('등록', total=10)
            self.view.progress.update(task, completed=4)
            self.assertIn('40%', text())
            self.view.progress.finish(task)
            self.assertEqual(text(), warning)
