"""Suggestion protocol, ghost input and explicit keyboard acceptance."""

import asyncio
from dataclasses import replace
import unittest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout
from prompt_toolkit.input import create_pipe_input

from hub.backend.followup import split_followup, INSTRUCTION
from hub.backend.context import HubContextBuilder
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from llm.core.models import Message, MessageRole, MessageStatus
from tests.hub.support import SizedOutput, eventually


class FollowupTests(unittest.IsolatedAsyncioTestCase):
    def test_metadata_is_trailing_bounded_and_never_code(self):
        tagged = "Answer\n<hub-next>Add a regression test</hub-next>"
        self.assertEqual(split_followup(tagged), ("Answer", "Add a regression test"))
        for text in ("```xml\n" + tagged + "\n```", tagged + "\nMore text",
                     "    <hub-next>Literal code</hub-next>",
                     "Answer\n<hub-next>" + "a" * 241 + "</hub-next>",
                     "Answer\n<hub-next>one\ntwo</hub-next>"):
            self.assertEqual(split_followup(text), (text, ""))
        for tail in ("<hub-", "<hub-next>", "<hub-next>Next"):
            self.assertEqual(split_followup("Answer\n" + tail, final=False), ("Answer", ""))
        self.assertEqual(split_followup("Answer\n<hub-next>Next"), ("Answer\n<hub-next>Next", ""))

    def test_protocol_is_context_only_and_old_hints_are_removed(self):
        user = Message("u", MessageRole.USER, "task", MessageStatus.COMMITTED)
        assistant = Message("a", MessageRole.ASSISTANT,
            "answer\n<hub-next>Next task</hub-next>", MessageStatus.COMPLETED)
        current = Message("v", MessageRole.USER, "new task", MessageStatus.COMMITTED)
        selected = HubContextBuilder().for_run([user, assistant, current], "v")
        self.assertEqual([m.id for m in selected], ["u", "a", "v"])
        self.assertEqual(selected[-2].content, "answer")
        self.assertIn("<hub-next>", assistant.content)
        self.assertEqual(current.content, "new task")
        from llm.services.history.context import ContextPolicy
        budgeted = HubContextBuilder().for_run([user, assistant, current], "v",
            policy=ContextPolicy(mode="budget", max_chars=18))
        self.assertEqual([m.id for m in budgeted], ["u", "a", "v"])

    async def test_protocol_processor_preserves_original_ids_for_resume(self):
        from hub.backend.completion import FollowupProcessor
        from llm.components.processing import CompletionMessage, CompletionRequest
        request = CompletionRequest({}, [CompletionMessage({"role": "user", "content": "task"}, "u")], 0)
        async for _ in FollowupProcessor().session(None).prepare(request):
            pass
        self.assertEqual(request.messages[0].value["content"], INSTRUCTION)
        self.assertIsNone(request.messages[0].source_id)
        self.assertEqual(request.messages[1].source_id, "u")
        self.assertEqual(request.messages[1].value["content"], "task")

    async def test_right_accepts_without_sending_and_switch_discards_hint(self):
        view = LiveHubView()
        with create_pipe_input() as pipe:
            app = Application(layout=Layout(view.container, focused_element=view.composer),
                key_bindings=view.navigation_keys, input=pipe, output=SizedOutput(), full_screen=True)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                answer = ChatMessage("assistant", "Answer\n<hub-next>Write tests</hub-next>", status="completed", id="a")
                snapshot = HubSnapshot("p", "P", "model", "file", (SessionSummary("s", "S", "idle"),), "s", (answer,))
                with set_app(app):
                    view.apply_snapshot(snapshot)
                self.assertEqual(view.composer.buffer.suggestion.text, "Write tests")
                self.assertEqual(view.transcript.control.messages[-1].text, "Answer")
                self.assertEqual(view.composer.text, "")
                view.composer.text = "different text"
                view.composer.buffer.cursor_position = 0
                pipe.send_text("\x1b[C")
                await eventually(lambda: view.composer.buffer.cursor_position == 1)
                self.assertEqual(view.composer.text, "different text")
                view.composer.text = ""
                pipe.send_text("\x1b[C")
                await eventually(lambda: view.composer.text == "Write tests")
                self.assertEqual(len(view.sessions[0].messages), 1)
                view.composer.text = ""
                with set_app(app):
                    view.apply_snapshot(replace(snapshot, selected_id="t", sessions=(SessionSummary("t", "T", "idle"),), messages=()))
                self.assertIsNone(view.composer.buffer.suggestion)
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()
