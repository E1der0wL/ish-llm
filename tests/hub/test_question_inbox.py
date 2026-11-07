"""Approved composer UX, durable observations, and delayed callback boundaries."""

import asyncio
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.interactions.question_values import QuestionTool
from hub.model import QuestionPresentation
from tests.hub.support import SizedOutput, eventually


class QuestionInboxTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.view.project_id = 'p'
        self.view.sessions[0] = replace(self.view.sessions[0], id='s')
        self.view.sessions[1] = replace(self.view.sessions[1], id='other')
        self.enterContext(set_app(self.app))
        self.calls = []
        self.view.on_question_action = lambda *args: self.calls.append(args)
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running)
        self.items = [QuestionPresentation.nonblocking_request('run', QuestionTool.request(
            {'question': title, 'choices': ['One', 'Two']}), run_status='running')
            for title in ('First question?', 'Second question?')]

    async def asyncTearDown(self):
        self.app.exit()
        await self.task

    def show(self, items=None):
        self.view.question.sync('p', 's', tuple(self.items if items is None else items))

    def receive(self, result):
        operation = self.calls[-1][2]
        self.calls[-1][-1](result)
        return operation

    async def test_click_and_ctrl_r_reopen_the_deferred_answer_in_the_composer(self):
        view, inbox = self.view, self.view.question.inbox
        view.composer.text = 'ordinary draft'
        self.show()
        view.composer.text = 'answer draft'
        view.question.return_to_input()
        self.app.invalidate()
        await eventually(lambda: inbox.open_button.window in self.app.layout.visible_windows)
        positions = self.app.renderer._last_screen.visible_windows_to_write_positions
        area = positions[inbox.open_button.window]
        x, y = area.xpos + area.width // 2 + 1, area.ypos + 1
        self.pipe.send_text(f'\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m')
        await eventually(lambda: bool(self.calls))
        self.assertEqual(self.receive(self.items), 'list')
        self.assertEqual(view.composer.text, 'answer draft')
        self.assertIs(self.app.layout.current_control, view.composer.control)
        view.question.return_to_input()
        self.assertEqual(view.composer.text, 'ordinary draft')
        self.pipe.send_text('\x12')
        await eventually(lambda: len(self.calls) == 2)
        self.assertEqual(self.receive(self.items), 'list')
        self.assertEqual(view.composer.text, 'answer draft')
        self.assertIsNone(view._dialog)

    async def test_switch_while_question_lookup_is_in_flight(self):
        view, question = self.view, self.view.question
        self.show()
        question.return_to_input()
        self.pipe.send_text('\x12')
        await eventually(lambda: len(self.calls) == 1)
        view.select(1)
        self.assertFalse(question.inbox.can_open_current())
        self.receive(self.items)  # Old session replies before new snapshot arrives.
        self.assertIsNone(question.item)
        question.sync('p', 'other', ())
        self.pipe.send_text('a\x12b')
        await eventually(lambda: view.composer.text == 'ab')
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('Ctrl+R', dict(view.bindings.hints()))
        view.select(0)
        self.show()
        question.return_to_input()
        self.pipe.send_text('\x12')
        await eventually(lambda: len(self.calls) == 2)
        self.assertEqual(self.calls[-1][:2], ('p', 's'))

    async def test_finished_question_hides_answer_button_and_restores_focus(self):
        self.show([{**self.items[0], 'run_status': 'paused'}])
        self.view.question.return_to_input()
        self.assertTrue(self.view.question.inbox.can_open_current())
        self.app.layout.focus(self.view.question.inbox.open_button)
        self.show([{**self.items[0], 'run_status': 'completed'}])
        self.assertFalse(self.view.question.inbox.can_open_current())
        self.assertFalse(self.view.question.inbox.container.filter())
        self.assertIs(self.app.layout.current_control, self.view.composer.control)

    async def test_tab_navigation_icons_and_drafts_without_extra_popup(self):
        self.view.composer.text = 'Ordinary draft'
        self.show()
        question, inbox = self.view.question, self.view.question.inbox
        self.assertIs(self.app.layout.current_control, self.view.composer.control)
        self.assertIsNone(self.view._dialog)
        self.view.composer.text = 'First draft'
        self.pipe.send_text('\t')
        await eventually(lambda: self.app.layout.current_control is inbox.navigation_control)
        self.pipe.send_text('\x1b[C')
        await eventually(lambda: inbox.position() == ' 2/2 ')
        self.assertEqual(self.view.composer.text, '')
        self.assertIs(self.app.layout.current_control, inbox.navigation_control)
        self.assertFalse(inbox.previous_button.control.is_focusable())
        self.assertFalse(inbox.next_button.control.is_focusable())
        self.pipe.send_text(' ')
        await eventually(lambda: self.app.layout.current_control is self.view.composer.control)
        self.view.composer.text = 'Second draft'
        inbox.previous_button.handler()
        self.assertEqual(self.view.composer.text, 'First draft')
        question.return_to_input()
        self.assertEqual(self.view.composer.text, 'Ordinary draft')
        self.show()
        self.assertIsNone(question.item)
        inbox.select(self.items[1]['request']['id'])
        self.assertEqual(self.view.composer.text, 'Second draft')

    async def test_finished_source_closes_editor_and_late_save_does_not_deliver(self):
        self.show([self.items[0]])
        question, inbox = self.view.question, self.view.question.inbox
        self.view.composer.text = 'My answer'
        question.answer()
        self.assertEqual(self.calls[-1][2], 'save')
        self.assertEqual(self.view.composer.text, '')
        question.answer()
        self.assertEqual(len(self.calls), 1)
        self.show([{**self.items[0], 'run_status': 'completed'}])
        self.assertIsNone(question.item)
        self.assertIn('My answer', self.view.composer.text)
        from llm.core.interactions import InteractionRequest
        response = InteractionRequest.from_dict(self.items[0]['request']).respond('reply', value='My answer')
        self.receive(response.to_dict())
        self.assertIsNone(question.item)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(inbox.outstanding())

    async def test_answer_saves_then_selects_original_target_without_automatic_broadcast(self):
        self.show([self.items[0]])
        question, inbox = self.view.question, self.view.question.inbox
        self.view.composer.text = 'My answer'
        question.answer()
        from llm.core.interactions import InteractionRequest
        response = InteractionRequest.from_dict(self.items[0]['request']).respond('reply', value='My answer')
        self.assertEqual(self.receive(response.to_dict()), 'save')
        self.assertEqual(self.calls[-1][2], 'targets')
        self.receive({'status': 'running', 'targets': [
            {'run_id': 'run', 'target_id': 'node1', 'label': 'First node'},
            {'run_id': 'run', 'target_id': 'node2', 'label': 'Second node'}]})
        self.assertIsNone(inbox.destination.value)
        inbox.deliver()
        self.assertEqual(len(self.calls), 2)
        inbox.destination.value = 1
        inbox.deliver()
        self.assertEqual(self.calls[-1][2], 'deliver')
        self.assertEqual(self.calls[-1][3]['targets'], ['node2'])
        self.assertEqual(self.calls[-1][3]['target_run_id'], 'run')
        inbox.deliver()
        self.assertEqual(len(self.calls), 3)
        self.receive({'request_id': 'receipt'})
        self.assertIsNone(question.item)
