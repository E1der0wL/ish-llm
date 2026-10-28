"""Received chunks render whole; history and terminal status never animate."""

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout

from hub.model import AnswerDraft, ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.widget.transcript.conversation import render_messages
from tests.hub.support import SizedOutput, eventually


class StreamOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_stream_and_verdict_keep_input_and_message_position(self):
        view = LiveHubView()
        with create_pipe_input() as pipe:
            app = Application(layout=Layout(view.container, focused_element=view.composer),
                key_bindings=view.navigation_keys, input=pipe, output=SizedOutput(), full_screen=True)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                answer = ChatMessage('assistant', '', status='streaming', id='answer')
                base = HubSnapshot('p', 'P', 'model', 'file', (SessionSummary('s', 'S', 'running'),), 's', ())
                control = view.output.transcript.control
                for draft in (AnswerDraft('First chunk'), AnswerDraft('First chunk plus more'),
                              AnswerDraft('First chunk plus more', AnswerDraft.REVIEWING)):
                    with set_app(app):
                        view.apply_snapshot(replace(base, messages=(replace(answer, draft=draft),)))
                    await eventually(lambda: control._cache_key == control._requested_key
                                     and any(draft.text in ''.join(f[1] for f in row) for row in control._lines))
                    self.assertEqual([m.id for m in control.messages], ['answer'])
                pipe.send_text('next request')
                await eventually(lambda: view.composer.text == 'next request')
                with set_app(app):
                    view.apply_snapshot(replace(base, messages=(replace(answer, text='Final answer', status='completed'),)))
                await eventually(lambda: control._cache_key == control._requested_key
                                 and any('Final answer' in ''.join(f[1] for f in row) for row in control._lines))
                self.assertEqual([m.id for m in control.messages], ['answer'])
                self.assertEqual(view.composer.text, 'next request')
                self.assertFalse(any('First chunk' in ''.join(f[1] for f in row) for row in control._lines))
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()

    async def test_whole_chunks_render_while_input_and_completion_remain_responsive(self):
        view = LiveHubView()
        seen = []
        def render(messages, *args, **kwargs):
            seen.extend(m.text for m in messages if m.id == "answer")
            return render_messages(messages, *args, **kwargs)
        with create_pipe_input() as pipe, patch("hub.widget.transcript.conversation.render_messages", render):
            app = Application(layout=Layout(view.container, focused_element=view.composer),
                key_bindings=view.navigation_keys, input=pipe, output=SizedOutput(), full_screen=True)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                answer = ChatMessage("assistant", "", status="streaming", id="answer")
                base = HubSnapshot("p", "P", "model", "file", (SessionSummary("s", "S", "running"),), "s", (answer,))
                control = view.output.transcript.control
                received = {""}
                for text, status in (("first chunk " * 30, "streaming"),
                                     ("first chunk " * 30 + "가e\u0301👩\u200d💻 tail", "completed")):
                    received.add(text)
                    with set_app(app):
                        view.apply_snapshot(replace(base, messages=(replace(answer, text=text, status=status),)))
                    await eventually(lambda: text in seen and control._cache_key == control._requested_key)
                    self.assertEqual(control.messages[-1].status, status)
                    self.assertTrue(set(seen) <= received)
                pipe.send_text("draft")
                await eventually(lambda: view.composer.text == "draft")
                with set_app(app):
                    token = view.deleting.start(("p", "s"), 1)
                    view.deleting.finish(token)
                await eventually(lambda: control._cache_key == control._requested_key)
                self.assertEqual(control.messages[-1].text, text)
                self.assertTrue(set(seen) <= received)
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()
