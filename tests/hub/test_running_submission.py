"""Active work never silently turns a draft into a queued request."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock
import tempfile

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.locales.keys import MessageKey
from hub.model import HubSnapshot, SessionSummary, SessionView
from hub.ui.live import LiveController
from hub.widget.dialog import PopupSize
from tests.hub.support import SizedOutput


class RunningSubmissionTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.enterContext(set_app(self.app))
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.controller = LiveController(self.view, HubConfig(root, auto_title=False))
        self.controller.app = self.app
        self.controller._call = Mock()
        self.controller._send = Mock()
        self.controller.steer = Mock()
        view = self.view
        view.project_id, view.connected, view.visible = 'p', True, True
        view.sessions = [SessionView('Room', id='s')]
        view.participant_ids = ('a', 'b')
        view.agent_titles = {'a': 'Coder', 'b': 'Reviewer'}
        view._last_snapshot = HubSnapshot('p', 'Project', '', 'file',
            (SessionSummary('s', 'Room', 'running'),), 's', (), active_run_ids=('r-a', 'r-b'))

    async def asyncTearDown(self):
        self.view.close()

    def open(self):
        self.view.composer.text = 'Additional request'
        self.controller.submit('s', self.view.composer.text)
        return self.view._dialog

    async def test_active_snapshot_opens_custom_choice_immediately_and_close_restores(self):
        popup = self.open()
        self.assertEqual(popup.size, PopupSize.CUSTOM)
        self.assertIsNotNone(popup.status_bar)
        self.assertEqual(popup._buttons[-1].text, self.view.t(MessageKey.SEARCH_CLOSE))
        self.assertEqual(self.view.composer.text, '')
        self.controller._call.assert_not_called()
        self.controller._send.assert_not_called()
        popup._buttons[-1].handler()
        self.assertIsNone(self.view._dialog)
        self.assertEqual(self.view.composer.text, 'Additional request')

    async def test_queue_submits_all_recipients_once_only_after_confirmation(self):
        popup = self.open()
        popup._buttons[0].handler()
        popup._buttons[0].handler()
        self.controller._send.assert_called_once()
        args = self.controller._send.call_args.args
        self.assertEqual(args[:5], ('submit_input', 's', 'Additional request', 'Additional request', ('a', 'b')))
        self.assertIs(args[-1], False)
        self.controller.steer.assert_not_called()

    async def test_broadcast_steering_requires_explicit_run_choice(self):
        popup = self.open()
        popup.body.current_value = 'steer'
        popup._buttons[0].handler()
        self.assertEqual(self.controller._call.call_args.args, ('running_recipients', 's', ('a', 'b')))
        ready = self.controller._call.call_args.kwargs['completed']
        ready((('r-a', 'a'), ('r-b', 'b')))
        self.controller.steer.assert_not_called()
        self.assertEqual(popup.body.values, [('r-a', 'Coder'), ('r-b', 'Reviewer')])
        popup.body.current_value = 'r-b'
        popup._buttons[0].handler()
        self.controller.steer.assert_called_once()
        self.assertEqual(self.controller.steer.call_args.kwargs['expected_run_id'], 'r-b')
        self.assertIsNone(self.view._dialog)

    async def test_close_while_resolving_targets_discards_late_result(self):
        popup = self.open()
        popup.body.current_value = 'steer'
        popup._buttons[0].handler()
        ready = self.controller._call.call_args.kwargs['completed']
        popup._buttons[-1].handler()
        ready((('r-a', 'a'),))
        self.controller.steer.assert_not_called()
        self.assertEqual(self.view.composer.text, 'Additional request')

    async def test_finished_work_does_not_silently_queue_steering(self):
        popup = self.open()
        popup.body.current_value = 'steer'
        popup._buttons[0].handler()
        self.controller._call.call_args.kwargs['completed'](())
        self.assertIs(self.view._dialog, popup)
        self.assertEqual(self.view.notice, self.view.t(MessageKey.NO_ACTIVE_RUN))
        self.controller._send.assert_not_called()
        self.controller.steer.assert_not_called()

    async def test_target_query_failure_keeps_draft_recoverable(self):
        popup = self.open()
        popup.body.current_value = 'steer'
        popup._buttons[0].handler()
        self.controller._call.call_args.kwargs['completed'](None)
        self.assertIs(self.view._dialog, popup)
        popup._buttons[-1].handler()
        self.assertEqual(self.view.composer.text, 'Additional request')

    async def test_running_recipients_uses_public_status_and_filters_current_recipient_ids(self):
        status = SimpleNamespace(active_runs=[SimpleNamespace(run_id='r-a', agent_id='a'),
                                             SimpleNamespace(run_id='r-c', agent_id='c')])
        run = SimpleNamespace(astatus=AsyncMock(return_value=status))
        runtime = HubRuntime.__new__(HubRuntime)
        runtime.sessions = {'s': SimpleNamespace(run=run)}
        self.assertEqual(await runtime.running_recipients('s', ('a', 'b')), (('r-a', 'a'),))
