"""Composer questions, built-in tool selection and nonblocking UI persistence."""

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.view_state import ViewStateStore, ViewStateWriter, ReadingPosition
from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.ui.application import create_application
from hub.ui.presentation import present
from llm.core.interactions import InteractionRequest, InteractionOption
from llm.engines.loop import LoopEngine
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_live import until
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class QuestionToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_builtin_cache_is_detached_and_selection_invalidates_it(self):
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.components.tools import Tool
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            try:
                await runtime.start()
                component = runtime.builtin_tools
                project = await runtime.project.aget_data()
                original = BuiltinToolComponent.resolve
                with patch.object(BuiltinToolComponent, "resolve", autospec=True, side_effect=original) as resolve:
                    first = component.resolve(project, "tools")
                    first.register(Tool("test_extra", "local only", {"type": "object"}, AsyncMock()))
                    second = component.resolve(project, "tools")
                    self.assertNotIn("test_extra", second.names())
                    self.assertIs(first.get("ask_user").handler, second.get("ask_user").handler)
                    self.assertEqual(first.contracts()["file_create"], second.contracts()["file_create"])
                    resolve.assert_not_called()
                    with self.assertRaises(ValueError):
                        second.prepare("file_read", '{"path": 42}')
                    project.config.parameters["components"]["builtin_tools"]["config"]["enabled"] = []
                    self.assertEqual(component.resolve(project, "tools").names(), ())
                    self.assertEqual(resolve.call_count, 1)
                    project.config.parameters["components"]["builtin_tools"]["config"]["enabled"] = ["ask_user"]
                    self.assertEqual(component.resolve(project, "tools").names(), ("ask_user",))
                    self.assertEqual(resolve.call_count, 2)
            finally:
                await runtime.close()

    async def test_enter_answers_in_original_composer_through_worker(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Which output?","choices":["Markdown","Text"]}', name="ask_user")], finish="tool_calls")],
            [chunk("Answer received", finish="stop")])
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            config = HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            app, controller = create_application(config, input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                with set_app(app):
                    controller.new_session("new", "Questions")
                await until(lambda: not view.no_sessions)
                pipe.send_text("Make a document\r")
                await until(lambda: view.question.item is not None)
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertIsNone(view._dialog)
                pipe.send_text("2\r")
                await until(lambda: view.question.item is None and any(m.text == "Answer received" for m in view.transcript.control.messages))
                self.assertIn("Text", str(provider.requests[-1]["messages"]))
                self.assertIsNone(view._dialog)
                self.assertEqual(view.composer.text, "")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_question_waits_and_answer_continues_same_run(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"question":"Which format?","choices":["Markdown","Text"]}', name="ask_user")], finish="tool_calls")],
            [chunk("Done with your choice", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                session_id = await runtime.new_session()
                await runtime.submit(session_id, "Write a document")
                await eventually(lambda: bool(runtime.builtin_tools.questions.pending))
                snapshot = await runtime.snapshot()
                item = snapshot.questions[0]
                self.assertEqual(snapshot.run.status, "running")
                self.assertEqual(len(provider.requests), 1)
                with self.assertRaises(ValueError):
                    await runtime.answer_question(runtime.project.id, "wrong-session", item["run_id"], item["request"], "reply", "Text")
                await runtime.answer_question(runtime.project.id, session_id, item["run_id"], item["request"], "reply", "Markdown")
                await runtime.sessions[session_id].run.wait_idle()
                self.assertEqual(len(await runtime.sessions[session_id].run.alist()), 1)
                self.assertIn("Markdown", str(provider.requests[-1]["messages"]))
                self.assertEqual((await runtime.snapshot()).messages[-1].text, "Done with your choice")
                self.assertFalse(runtime.builtin_tools.questions.pending)
            finally:
                await runtime.close()

    async def test_builtin_selection_persists_and_approval_resumes(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call('{"path":"answer.txt","content":"approved"}', name="file_create")], finish="tool_calls")],
            [chunk("Created", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(Path(root) / "workspace", file_root=root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                entries = await runtime.manage_tools(runtime.project.id, "list")
                self.assertTrue(entries and all(item["enabled"] for item in entries))
                selected = next(item for item in entries if item["name"] == "builtin:file_read")
                entries = await runtime.manage_tools(runtime.project.id, "toggle", selected["name"], selected["version"])
                self.assertFalse(next(item for item in entries if item["name"] == selected["name"])["enabled"])
                with self.assertRaisesRegex(ValueError, "changed"):
                    await runtime.manage_tools(runtime.project.id, "toggle", selected["name"], selected["version"])
                sid = await runtime.new_session()
                await runtime.submit(sid, "Create the file")
                await runtime.sessions[sid].run.wait_idle()
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.run.status, "paused")
                self.assertFalse((Path(root) / "answer.txt").exists())
                item = snapshot.questions[0]
                action = present(snapshot, runtime.t).messages[-1]
                self.assertIn('"path": "answer.txt"', action.text)
                self.assertIn('"content": "approved"', action.text)
                await runtime.answer_question(runtime.project.id, sid, item["run_id"], item["request"], "approve", None)
                await runtime.sessions[sid].run.wait_idle()
                self.assertEqual((Path(root) / "answer.txt").read_text(), "approved")
                self.assertEqual((await runtime.snapshot()).run.status, "completed")
            finally:
                await runtime.close()
            reopened = HubRuntime(config)
            try:
                await reopened.start()
                entries = await reopened.manage_tools(reopened.project.id, "list")
                self.assertFalse(next(item for item in entries if item["name"] == "builtin:file_read")["enabled"])
            finally:
                await reopened.close()

    async def test_question_interrupt_clears_waiter(self):
        provider = ScriptedCompletion([chunk(calls=[call('{"question":"Wait?"}', name="ask_user")], finish="tool_calls")])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)}))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                await runtime.submit(sid, "Ask")
                await eventually(lambda: bool(runtime.builtin_tools.questions.pending))
                await runtime.interrupt(sid)
                await runtime.sessions[sid].run.wait_idle()
                self.assertFalse(runtime.builtin_tools.questions.pending)
                self.assertEqual((await runtime.snapshot()).run.status, "interrupted")
            finally:
                await runtime.close()

    async def test_ctrl_c_noop_full_screen_tag_and_escape(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, _ = create_preview(input=pipe, output=output)
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                view.composer.text = "keep this"
                view.composer.buffer.cursor_position = len(view.composer.text)
                pipe.send_text("\x03z")
                await eventually(lambda: view.composer.text.endswith("z"))
                self.assertTrue(view.visible)
                self.assertEqual(view.composer.text, "keep thisz")
                view.transcript.set_messages((ChatMessage("assistant", "```py\nprint(1)\n```", id="m"),))
                app.invalidate()
                await eventually(lambda: bool(view.transcript.control.objects))
                pipe.send_text("\x14\r")
                await eventually(lambda: view._dialog is not None and view._dialog.output.window.render_info is not None)
                info = view._dialog.output.window.render_info
                # Preview insets Hub by 1 row / 2 columns on each side.
                self.assertEqual(info.window_height, output.rows - 4)
                self.assertGreaterEqual(info.window_width, output.columns - 7)
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is None)
                self.assertIs(app.layout.current_control, view.tags.control)
                pipe.send_text("\x1b")
                await eventually(lambda: not view.tags.visible)
                self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                app.exit()
                await task

    async def test_question_composer_preserves_chat_and_answer_drafts(self):
        view = LiveHubView()
        request = InteractionRequest("Format?", (InteractionOption("reply", "Reply"),), kind="input").to_dict()
        snapshot = HubSnapshot(project_id="p", project_title="Project", model="test/model", storage="file",
            sessions=(SessionSummary("s", "Session", "running", 0),), selected_id="s", messages=())
        view.apply_snapshot(snapshot)
        view.composer.text = "ordinary draft"
        item = {"run_id": "r", "live": True, "request": request}
        view.apply_snapshot(replace(snapshot, questions=(item,)))
        self.assertEqual(view.composer.text, "")
        view.composer.text = "answer draft"
        view.composer.buffer.cursor_position = 3
        view.apply_snapshot(replace(snapshot, questions=(item,)))
        self.assertEqual(view.composer.text, "answer draft")
        self.assertEqual(view.composer.buffer.cursor_position, 3)
        view.apply_snapshot(snapshot)
        self.assertEqual(view.composer.text, "ordinary draft")

    async def test_new_snapshot_respects_scroll_before_next_paint(self):
        view = LiveHubView()
        snapshot = HubSnapshot(project_id="p", project_title="Project", model="test/model", storage="file",
            sessions=(SessionSummary("s", "Session", "running", 0),), selected_id="s",
            messages=(ChatMessage("assistant", "paragraph\n\n" * 70, id="m"),))
        view.apply_snapshot(snapshot)
        control = view.transcript.control
        control.create_content(80, 10)
        # The last paint was at the bottom, then Home arrived before repaint.
        view.transcript.window.render_info = NS(content_height=len(control._lines), last_visible_line=lambda: len(control._lines) - 1)
        control.scroll("home")
        view.apply_snapshot(replace(snapshot, messages=(*snapshot.messages, ChatMessage("assistant", "new", id="n"))))
        control.create_content(80, 10)
        self.assertEqual(control.top_line, 0)

    async def test_slow_view_save_does_not_block_caller_and_flushes_latest(self):
        with tempfile.TemporaryDirectory() as root:
            store = ViewStateStore(root)
            entered, release = threading.Event(), threading.Event()
            original = store.save
            def slow(*args):
                entered.set()
                release.wait(5)
                original(*args)
            store.save = slow
            errors = []
            writer = ViewStateWriter(store, errors.append)
            writer.save("p", "first", {"s": ReadingPosition(line=1)})
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            writer.save("p", "second", {"s": ReadingPosition(line=2)})
            writer.save("p", "last", {"s": ReadingPosition(line=3)})
            self.assertEqual(writer.load("p"), ("last", {"s": ReadingPosition(line=3)}))
            release.set()
            await asyncio.to_thread(writer.close)
            self.assertFalse(errors)
            self.assertEqual(store.load("p"), ("last", {"s": ReadingPosition(line=3)}))
