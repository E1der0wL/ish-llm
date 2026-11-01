"""Terminal review is explicit evidence selection, never process execution."""

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, AsyncMock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from examples.hub.preview import create_preview
from tests.hub.support import SizedOutput
from hub.ui.chat.terminal import TerminalReview
from hub.backend import terminal
from llm.core.models import StepKind


class TerminalReviewTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app, self.view, _ = create_preview(input=DummyInput(), output=SizedOutput())
        self.app.loop = asyncio.get_running_loop()
        self.enterContext(set_app(self.app))
        self.view.visible = True
        self.view.project_id = 'project'

    async def asyncTearDown(self):
        self.view.close()

    async def test_page_size_and_review_evidence_are_explicit(self):
        controller = SimpleNamespace(view=self.view, submit=Mock(), _call=Mock())
        review = TerminalReview(controller)
        review.open()
        review.target = ['process', 'identifier']
        review.read()
        controller._call.assert_not_called()
        review.limit.text = '5'
        review.read()
        call = controller._call.call_args
        self.assertEqual(call.args[0], 'terminal_page')
        self.assertEqual(call.args[-1], 5)
        evidence = {'source': {'project_id': 'project', 'process_id': 'identifier'},
                    'page': {'stdout': 'hello', 'next_stdout_offset': 5, 'next_stderr_offset': 0}}
        call.kwargs['completed'](evidence)
        review.capture()
        review.review()
        controller.submit.assert_not_called()
        review.question.text = 'Review this output'
        review.review()
        submitted = controller.submit.call_args.args
        self.assertEqual(submitted[0], review.scope[1])
        self.assertIn('hello', submitted[1])
        self.assertIn('Review this output', submitted[1])
        self.assertEqual(controller._call.call_count, 1)

    async def test_closed_popup_discards_late_observation(self):
        controller = SimpleNamespace(view=self.view, submit=Mock(), _call=Mock())
        review = TerminalReview(controller)
        review.open()
        review.target, review.limit.text = ['process', 'id'], '1'
        review.read()
        callback = controller._call.call_args.kwargs['completed']
        self.view.close_dialog()
        callback({'source': {}, 'page': {'stdout': 'late'}})
        self.assertIsNone(review.last_page)

    async def test_step_read_uses_public_observation_and_retains_data_references(self):
        step = SimpleNamespace(id='step', name='file_read', kind=StepKind.TOOL, status='completed',
            output=SimpleNamespace(data={'source': {'component': 'data', 'id': 'large'}, 'text': '한글'}), error=None)
        run = SimpleNamespace(id='run', steps=SimpleNamespace(aload=AsyncMock(return_value=step)))
        runs = SimpleNamespace(aload=AsyncMock(return_value=run), alist=AsyncMock(return_value=[run]))
        session = SimpleNamespace(project=SimpleNamespace(id='project'), runs=runs)
        runtime = SimpleNamespace(sessions={'session': session}, builtin_tools=None)
        catalog = await terminal.catalog(runtime, 'session')
        self.assertEqual(catalog['runs'], ['run'])
        self.assertIsNotNone(catalog['process_error'])
        page = await terminal.page(runtime, 'session', ['step', 'run', 'step'], {'text': 0}, 1000)
        self.assertIn('large', page['page']['text'])
        self.assertIn('한글', page['page']['text'])
        self.assertEqual(page['source']['step_id'], 'step')
        small = await terminal.page(runtime, 'session', ['step', 'run', 'step'], {'text': 0}, 5)
        self.assertEqual(small['page']['text'], page['page']['text'][:5])
        step.kind = StepKind.LLM
        with self.assertRaises(ValueError):
            await terminal.page(runtime, 'session', ['step', 'run', 'step'], {'text': 0}, 5)
