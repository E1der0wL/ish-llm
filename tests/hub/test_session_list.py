"""Sidebar attention states are observations, not backend execution decisions."""

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import PropertyMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput

from examples.hub.preview import create_preview
from hub.asset.icon import NERD
from hub.model import HubSnapshot, SessionSummary, SessionView
from hub.widget.session_list import SessionListState as State
from tests.hub.support import SizedOutput


class SessionListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.addCleanup(self.view.close)
        self.view.project_id = 'p'
        self.view.sessions = [SessionView('Selected', id='s'), SessionView('Other', id='b')]
        self.view.selected = 0
        self.control = self.view._session_control

    def observe(self, *items):
        self.control.observe(HubSnapshot(self.view.project_id, 'Project', '', 'file',
                                        items, 's', ()))

    def test_raw_run_outcomes_are_preserved_and_attention_uses_pending_input(self):
        for item, expected in (
            (SessionSummary('s', 'Idle', 'idle'), State.IDLE),
            (SessionSummary('s', 'Queued', 'idle', queued_count=1), State.RUNNING),
            (SessionSummary('s', 'Active', 'running', active=True), State.RUNNING),
            (SessionSummary('s', 'Question', 'running', active=True, pending_input=True), State.INPUT_REQUIRED),
            (SessionSummary('s', 'Approval', 'waiting_approval'), State.INPUT_REQUIRED),
            (SessionSummary('s', 'Completed', 'idle', latest_run_id='r', latest_run_status='completed'), State.COMPLETED),
            (SessionSummary('s', 'Failed', 'idle', latest_run_id='r', latest_run_status='failed'), State.IDLE),
            (SessionSummary('s', 'Stopped', 'interrupted', latest_run_id='r', latest_run_status='interrupted'), State.IDLE),
        ):
            with self.subTest(expected=expected, item=item):
                self.observe(item)
                self.assertEqual(self.control.state('s'), expected)
                self.assertIs(self.control.observations['s'], item)

    def test_completion_is_read_only_after_viewing_home_and_new_run_is_unread(self):
        done = SessionSummary('s', 'Completed', 'idle', latest_run_id='r', latest_run_status='completed')
        self.observe(done)
        self.control.rows()
        self.assertEqual(self.control.state('s'), State.COMPLETED)
        self.view.visible = True
        self.view.settings_open = True
        self.control.rows()
        self.assertEqual(self.control.state('s'), State.COMPLETED)
        self.view.settings_open = False
        with patch.object(type(self.view), '_dialog', new_callable=PropertyMock, return_value=object()):
            self.control.rows()
            self.assertEqual(self.control.state('s'), State.COMPLETED)
        self.control.rows()
        self.assertEqual(self.control.state('s'), State.IDLE)
        self.observe(replace(done, latest_run_id='new'))
        self.assertEqual(self.control.state('s'), State.COMPLETED)
        self.control.rows()
        self.view.project_id = 'another-project'
        self.observe(done)
        self.assertEqual(self.control.state('s'), State.COMPLETED)
        self.assertEqual(NERD.session_idle, '')

    async def test_only_visible_running_rows_animate_without_backend_polling(self):
        self.view.visible = True
        self.observe(SessionSummary('s', 'Running', 'running', active=True))
        with set_app(self.app):
            with patch('hub.widget.session_list.monotonic', return_value=10):
                first = self.control.rows()[0].detail
            with patch('hub.widget.session_list.monotonic', return_value=10.2):
                second = self.control.rows()[0].detail
            self.assertNotEqual(first, second)
            self.control.create_content(25, None)
            self.assertIsNone(self.control._timer)
            def paint():
                self.app.render_counter += 1
                self.app.renderer.render(self.app, self.app.layout)
                self.app.after_render.fire()

            paint()
            timer = self.control._timer
            self.assertIsNotNone(timer)
            paint()
            self.assertIs(self.control._timer, timer)
            self.view.settings_open = True
            # Other render observers must still run when animation is stopped.
            observed = []
            self.app.after_render += lambda app: observed.append(app.render_counter)
            paint()
            self.assertTrue(observed)
            self.assertTrue(timer.cancelled())
            self.assertIsNone(self.control._timer)
            self.view.settings_open = False
            paint()
            timer = self.control._timer
            self.view.hide(self.app)
            self.assertTrue(timer.cancelled())
            await asyncio.sleep(0)

    async def test_offscreen_running_session_does_not_schedule_animation(self):
        self.view.visible = True
        self.view.sessions = [SessionView(str(index), id=str(index)) for index in range(40)]
        self.observe(SessionSummary('39', 'Hidden', 'running', active=True))
        with set_app(self.app):
            self.app.renderer.render(self.app, self.app.layout)
            self.app.after_render.fire()
            self.assertIsNone(self.control._timer)
            self.view.selected = 39
            self.app.layout.focus(self.control)
            self.app.render_counter += 1
            self.app.renderer.render(self.app, self.app.layout)
            self.app.after_render.fire()
            window = next(window for window in self.app.layout.visible_windows if window.content is self.control)
            # Focusing the last title can leave its detail below the viewport.
            # Reveal the spinner row before expecting its animation to run.
            window.vertical_scroll += self.control.ROW_HEIGHT
            self.app.render_counter += 1
            self.app.renderer.render(self.app, self.app.layout)
            self.app.after_render.fire()
            self.assertIsNotNone(self.control._timer)

    def test_one_spinner_frame_and_one_projection_are_shared_by_render_consumers(self):
        self.view.visible = True
        self.view.sessions = [SessionView(str(index), id=str(index)) for index in range(40)]
        self.observe(*(SessionSummary(str(index), str(index), 'running', active=True)
                       for index in range(40)))
        with set_app(self.app), patch('hub.widget.session_list.monotonic', return_value=10):
            with patch.object(self.control.spinner, 'render', wraps=self.control.spinner.render) as frame:
                rows = self.control.rows()
                self.control.fragments()
                self.control.cursor_position()
                self.assertIs(self.control.rows(), rows)
                self.assertEqual(frame.call_count, 1)
                self.assertEqual(len({row.detail for row in rows}), 1)
        self.view.theme = replace(self.view.theme, progress_refresh_interval=.01)
        self.assertGreaterEqual(self.control.interval, self.control.spinner.interval / 1000)
