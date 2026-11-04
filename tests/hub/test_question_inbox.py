"""Approved composer UX, durable observations, and delayed callback boundaries."""

import asyncio
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
