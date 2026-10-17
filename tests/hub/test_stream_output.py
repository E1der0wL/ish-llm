"""Received chunks render whole; history and terminal status never animate."""

import asyncio
from dataclasses import replace
import unittest
from unittest.mock import patch

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout

from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.widget.transcript.conversation import render_messages
from tests.hub.support import SizedOutput, eventually


class StreamOutputTests(unittest.IsolatedAsyncioTestCase):
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
