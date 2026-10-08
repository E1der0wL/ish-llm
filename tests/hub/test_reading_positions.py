from hub.ui.presentation import present
from tests.hub.test_mockup import minimize
"""Durable UI positions, message metadata, and bounded draft preview."""

import asyncio
from dataclasses import replace
from datetime import datetime
from functools import partial
from pathlib import Path
import tempfile
import threading
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.ui.application import create_application
from hub.widget.conversation import ChatMessage, ConversationControl
from hub.config.profile import UserProfile
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.theme import HubTheme
from hub.config.view_state import ReadingPosition, ViewStateStore
from hub.backend.worker import BackendWorker
from tests.hub.test_live import controlled_backend, until
from tests.hub.test_mockup import SizedOutput
from examples.hub.preview import create_preview


class ReadingPositionTests(unittest.TestCase):
    def test_scroll_after_snapshot_before_paint_uses_new_content(self):
        control = ConversationControl((), HubTheme())
        control.create_content(80, 10)
        control.messages = (ChatMessage("assistant", "paragraph\n\n" * 70, id="new"),)
        control.follow_tail = True
        control.scroll("home")
        for _ in range(5):
            control.scroll("down")
        self.assertEqual(control.top_line, 5)
        control.create_content(80, 10)
        self.assertEqual(control.top_line, 5)

    def test_anchor_survives_wrapping_changes_and_inserted_messages(self):
        messages = (ChatMessage("assistant", "long content " * 150, id="before"),
                    ChatMessage("assistant", "target\n\n" * 30, id="target"))
        control = ConversationControl(messages, HubTheme())
        control.create_content(100, 8)
        target = dict(control._anchors)["target"]
        control._scroll_to(target + 2)
        position = control.reading_position()
        control.messages = (ChatMessage("assistant", "inserted\n\n" * 10, id="inserted"), *messages)
        control.create_content(50, 8)
        self.assertEqual(control.reading_position(), replace(position, line=dict(control._anchors)["target"] + 2))

    def test_positions_are_isolated_by_project_and_session(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ViewStateStore(directory)
            first = ReadingPosition("message-a", 3, 9)
            second = ReadingPosition("message-b", 2, 20)
            store.save("project-a", "one", {"one": first})
            store.save("project-b", "one", {"one": second})
            store.save("project-a", "two", {"two": second})
            reopened = ViewStateStore(directory)
            self.assertEqual(reopened.load("project-a"), ("two", {"one": first, "two": second}))
            self.assertEqual(reopened.load("project-b"), ("one", {"one": second}))
            self.assertFalse(list(store.path.parent.glob("*.tmp")))


class ReadingPositionUITests(unittest.IsolatedAsyncioTestCase):
    async def test_positions_survive_session_switch_hide_and_process_reopen(self):
        gate = threading.Event()
        gate.set()
        backend = partial(controlled_backend, gate=gate)
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(directory, "loop", "test/model", auto_title=False,
                               user_profile=UserProfile("my-account"))
            runtime = HubRuntime(config, backend)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                first = runtime.selected_id
                await runtime.submit(first, "First\n\n" + "paragraph\n\n" * 70)
                await runtime.sessions[first].run.wait_idle()
                snapshot = await runtime.snapshot()
                user = next(message for message in snapshot.messages if message.role == "user")
                stored = (await runtime.sessions[first].aconversation())[0]
                self.assertEqual(user.author, "my-account")
                self.assertEqual(user.status, "committed")
                self.assertEqual(user.id, stored.id)
                self.assertEqual(user.time, stored.created_at)
                self.assertEqual(present(snapshot, runtime.t).messages[0].time, datetime.fromisoformat(stored.created_at).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
                second = await runtime.new_session("new", "Second")
                await runtime.submit(second, "Second\n\n" + "paragraph\n\n" * 70)
                await runtime.sessions[second].run.wait_idle()
            finally:
                await runtime.close()

            positions = {}
            for reopened in (False, True):
                with create_pipe_input() as pipe:
                    app, controller = create_application(config, input=pipe, output=SizedOutput(rows=24),
                        worker_factory=partial(BackendWorker, backend_factory=backend))
                    view = controller.view
                    task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
                    async def loaded(identifier, prefix):
                        await until(lambda: view.connected and view.sessions[view.selected].id == identifier
                                    and any(message.text.startswith(prefix) for message in view.transcript.control.messages)
                                    and view.transcript.window.render_info is not None
                                    and not view.transcript.control.restoring)
                    try:
                        if reopened:
                            await loaded(first, "First")
                            self.assertEqual(view.transcript.control.reading_position(), positions[first])
                            view.select(next(i for i, session in enumerate(view.sessions) if session.id == second))
                            await loaded(second, "Second")
                            self.assertEqual(view.transcript.control.reading_position(), positions[second])
                        else:
                            await loaded(second, "Second")
                            for identifier, prefix, line in ((second, "Second", 15), (first, "First", 5)):
                                view.select(next(i for i, session in enumerate(view.sessions) if session.id == identifier))
                                await loaded(identifier, prefix)
                                pipe.send_text("\x1b\x1b[H" + "\x1b\x1b[B" * line)
                                await until(lambda: view.transcript.control.top_line == line)
                                positions[identifier] = view.transcript.control.reading_position()
                                store = ViewStateStore(directory)
                                await until(lambda: store.path.exists() and
                                            store.load(view.project_id)[1].get(identifier) == positions[identifier])
                            await minimize(pipe, view)
                            await until(lambda: not view.visible)
                            await asyncio.wrap_future(controller.worker.call("submit", first, "Hidden addition", "loop"))
                            await until(lambda: any(message.role == "assistant" and "Hidden addition" in message.text
                                                    for message in view.transcript.control.messages))
                            pipe.send_text("\x11")
                            await loaded(first, "First")
                            self.assertEqual(view.transcript.control.reading_position(), positions[first])
                    finally:
                        app.exit()
                        await task
                        await asyncio.to_thread(controller.close)
            self.assertTrue((Path(directory) / ".hub/view-state.json").is_file())

    async def test_preview_is_opt_in_and_does_not_overlap_transcript(self):
        with create_pipe_input() as pipe:
            output = SizedOutput(rows=24)
            app, view, _ = create_preview(input=pipe, output=output)
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: app.is_running)
                view.composer.text = "# draft\n\n" + "preview\n\n" * 100
                app.invalidate()
                await until(lambda: view.transcript.window.render_info is not None)
                self.assertFalse(view._preview_container.filter())
                pipe.send_text("\x12")  # F5
                await until(lambda: view.show_preview)
                for rows in (24, 40, 18, 32):
                    rendered.clear()
                    output.rows = rows
                    app._on_resize()
                    await asyncio.wait_for(rendered.wait(), 3)
                    positions = app.renderer._last_screen.visible_windows_to_write_positions
                    transcript = positions[view.transcript.window]
                    composer = positions[view.composer.window]
                    self.assertGreaterEqual(transcript.height, 5)
                    self.assertLessEqual(transcript.ypos + transcript.height, composer.ypos)
                    if view._preview_container.filter():
                        preview = positions[view.draft_preview.window]
                        self.assertLessEqual(transcript.ypos + transcript.height, preview.ypos)
                        self.assertLessEqual(preview.ypos + preview.height, composer.ypos)
                        self.assertLessEqual(preview.height, 3)
            finally:
                app.exit()
                await task
