"""Remembering permission is an explicit, cancelable UI action."""

import asyncio
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.interactions.approval_management import ApprovalAction, ApprovalCatalog
from hub.backend.interactions.remembered import ApprovalMatch, RememberScope
from hub.locales.keys import MessageKey
from hub.widget.forms.approval_manager import ApprovalManager, ApprovalTab
from hub.widget.forms.approval_selection import ApprovalSelectionDialog
from hub.widget.dialog import Dialog, PopupSize
from hub.widget.controls import Button, TextArea
from llm.core.interactions import approval_request
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_remembered_approvals import action


class ApprovalUITests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.enterContext(set_app(self.app))
        self.view.on_answer = Mock()
        self.view.on_manage_approvals = Mock()
        self.addCleanup(self.view.close)
        self.addCleanup(self.view.progress.close)

    def question(self, *, command='git status --short | head -n 30'):
        request = approval_request('Review', category='tool.execute', action=action(command))
        self.view.question.sync('p', 's', ({'run_id': 'r', 'live': False, 'request': request.to_dict()},))
        return self.view.question

    def choose(self, number):
        self.view.composer.text = str(number)
        self.view.question.answer()
        return self.view._dialog

    async def keyboard(self):
        task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        async def stop():
            if self.app.is_running:
                self.app.exit()
            await task
        self.addAsyncCleanup(stop)
        await eventually(lambda: self.app.is_running)
        self.app.layout.focus(self.view.composer)

    async def test_number_key_approves_without_enter_and_invalid_number_is_ignored(self):
        self.question()
        await self.keyboard()
        self.pipe.send_text('9')
        await asyncio.sleep(.05)
        self.view.on_answer.assert_not_called()
        self.assertEqual(self.view.composer.text, '')
        self.pipe.send_text('1')
        await eventually(lambda: self.view.on_answer.called)
        self.assertEqual(self.view.on_answer.call_args.args[1], 'approve')
        self.assertIsNone(self.view._dialog)

    async def test_number_key_for_range_opens_review_without_granting_permission(self):
        self.question()
        await self.keyboard()
        self.pipe.send_text('3')
        await eventually(lambda: isinstance(self.view._dialog, ApprovalSelectionDialog))
        self.view.on_answer.assert_not_called()
        self.assertEqual(self.view._dialog.match, ApprovalMatch.COMMAND_RANGE)

    async def test_extra_approval_input_and_questions_keep_enter_submission(self):
        request = replace(approval_request('Review', action=action()), input_schema={'type': 'string'})
        self.view.question.sync('p', 's', ({'run_id': 'r', 'live': False, 'request': request.to_dict()},))
        self.assertFalse(self.view.question.immediate_choices)
        await self.keyboard()
        self.pipe.send_text('1 note')
        await eventually(lambda: self.view.composer.text == '1 note')
        self.view.on_answer.assert_not_called()
        self.pipe.send_text('\r')
        await eventually(lambda: self.view.on_answer.called)
        self.assertEqual(self.view.on_answer.call_args.args[2], 'note')
        self.view.on_answer.reset_mock()
        self.view.question.sync('p', 's', ({'run_id': 'r', 'live': True,
            'request': replace(request, kind='input').to_dict()},))
        self.assertFalse(self.view.question.immediate_choices)
        self.view.composer.text = ''
        self.pipe.send_text('1')
        await eventually(lambda: self.view.composer.text == '1')
        self.view.on_answer.assert_not_called()

    async def test_bracketed_paste_does_not_approve(self):
        self.question()
        await self.keyboard()
        self.pipe.send_bytes(b'\x1b[200~1\x1b[201~')
        await eventually(lambda: self.view.composer.text == '1')
        self.view.on_answer.assert_not_called()

    async def test_apply_label_and_exact_scope_are_only_submitted_after_confirmation(self):
        question = self.question()
        popup = self.choose(2)
        self.assertIsInstance(popup, ApprovalSelectionDialog)
        self.assertEqual(popup.size, PopupSize.MEDIUM)
        self.assertEqual(popup._buttons[0].text, '적용')
        self.assertEqual(popup.scope.current_value, RememberScope.SESSION)
        self.view.on_answer.assert_not_called()
        popup.scope.current_value = RememberScope.PROJECT
        popup._buttons[0].handler()
        self.view.on_answer.assert_called_once()
        item, option, value, selection = self.view.on_answer.call_args.args
        self.assertEqual(item, question.item)
        self.assertEqual(option, 'approve')
        self.assertEqual(selection.scope, RememberScope.PROJECT)
        self.assertEqual(selection.match, ApprovalMatch.EXACT)
        self.assertIsNone(self.view._dialog)

    async def test_range_cancel_and_stale_question_never_send_approval(self):
        self.question()
        popup = self.choose(3)
        self.assertEqual(popup.prefixes.text, 'git status\nhead')
        popup._buttons[1].handler()
        self.view.on_answer.assert_not_called()
        popup = self.choose(3)
        self.question(command='git log')
        popup.confirm()
        self.view.on_answer.assert_not_called()
        self.assertEqual(popup.status, self.view.t(MessageKey.APPROVAL_REQUEST_CHANGED))

    async def test_range_rejects_nonmatching_prefix_without_closing_then_applies_valid_prefix(self):
        self.question()
        popup = self.choose(3)
        popup.prefixes.text = 'git log\nhead'
        popup.confirm()
        self.assertIs(self.view._dialog, popup)
        self.view.on_answer.assert_not_called()
        self.assertEqual(popup.status, self.view.t(MessageKey.APPROVAL_INVALID_RANGE))
        popup.prefixes.text = 'git status --short\nhead -n'
        popup.confirm()
        selection = self.view.on_answer.call_args.args[-1]
        self.assertEqual(selection.prefixes, (('git', 'status', '--short'), ('head', '-n')))

    async def test_once_and_deny_use_existing_response_path(self):
        self.question()
        self.choose(1)
        self.assertIsNone(self.view._dialog)
        self.assertEqual(self.view.on_answer.call_args.args[1], 'approve')
        self.assertEqual(len(self.view.on_answer.call_args.args), 3)
        self.choose(4)
        self.assertEqual(self.view.on_answer.call_args.args[1], 'deny')

    async def test_manager_has_no_refresh_button_and_keeps_parent_open(self):
        parent = Dialog(title='Settings', body=TextArea(), buttons=[Button('Close')])
        self.view.dialogs.show(parent, parent.body)
        manager = ApprovalManager(self.view, 'p', pending=True)
        manager.open()
        args = self.view.on_manage_approvals.call_args
        self.assertEqual(args.args[:2], ('p', ApprovalAction.LIST))
        self.assertTrue(args.args[2]['include_interactions'])
        args.args[3](ApprovalCatalog((), (), True, 'app'))
        self.assertTrue(all(value is None for value in manager.selected.values()))
        for tab, expected in ((ApprovalTab.RULES, 4), (ApprovalTab.PENDING, 5)):
            buttons = manager.action_groups[tab].buttons
            self.assertEqual(len(buttons), expected)
            self.assertFalse(any('새로고침' in button.text for button in buttons))
        self.assertIsNotNone(manager.status_bar)
        manager.close()
        self.assertIs(self.view._dialog, parent)

    async def test_switching_tab_during_catalog_read_fetches_pending_only_after_read_completes(self):
        manager = ApprovalManager(self.view, 'p')
        manager.open()
        first = self.view.on_manage_approvals.call_args.args[3]
        manager.tabs.select(ApprovalTab.PENDING)
        self.assertEqual(self.view.on_manage_approvals.call_count, 1)
        first(ApprovalCatalog((), (), True, 'app'))
        self.assertEqual(self.view.on_manage_approvals.call_count, 2)
        self.assertTrue(self.view.on_manage_approvals.call_args.args[2]['include_interactions'])
        self.view.on_manage_approvals.call_args.args[3](ApprovalCatalog((), (), True, 'app'))
        self.assertFalse(manager.busy)
