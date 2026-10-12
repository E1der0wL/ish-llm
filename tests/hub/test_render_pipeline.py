"""Work-count and synchronization invariants; no machine-speed assertions."""

import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.worker import BackendWorker
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage, StepSummary, TraceItem
from hub.ui.output import RendererRegistry
from hub.ui.output.worker import RenderWorker
from hub.widget.conversation import render_messages
from hub.widget.conversation import ConversationControl
from hub.widget.message_cache import MessageRenderCache, frozen_rows
from hub.widget.progress import TaskProgress
from hub.ui.presentation import MessagePresentationCache
from hub.model import HubSnapshot
from tests.hub.support import SizedOutput, eventually


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = MessageRenderCache()
        self.registry = RendererRegistry()
        self.theme, self.language = HubTheme(), Language()
        self.addCleanup(self.registry.close)

    def prepare(self, messages, width=80):
        return self.cache.prepare(messages, width, self.theme, self.language,
            render=render_messages, renderers=self.registry, root=Path.cwd(), invalidate=lambda: None)

    def test_trace_changes_render_only_the_changed_item(self):
        trace = (TraceItem("c1", "reasoning", "**First thought**"),
                 TraceItem("t1", "tool", "file_read", "completed"),
                 TraceItem("c2", "reasoning", "Second thought"))
        thought = ChatMessage("reasoning", "combined fallback", id="a:reasoning", trace=trace)
        answer = ChatMessage("assistant", "Answer", id="a", status="streaming")
        self.prepare((thought, answer))
        response = self.cache.items["a"]["frame"]
        previous = self.cache.items["a:reasoning"]["trace_cache"]
        bodies, headers = self.cache.body_renders, self.cache.header_renders
        for index in range(10):
            thought = replace(thought, text=f"fallback {index}", trace=(*trace[:-1], replace(trace[-1], text=f"Second {index}")))
            frame = self.prepare((thought, answer))
            self.assertIs(self.cache.items["a"]["frame"], response)
            self.assertIs(self.cache.items["a:reasoning"]["trace_cache"][trace[0]], previous[trace[0]])
        self.assertEqual(self.cache.trace_renders, 13)
        self.assertEqual(self.cache.body_renders, bodies)
        self.assertEqual(self.cache.header_renders, headers)
        expected = render_messages((thought, answer), 80, self.theme, self.language, renderers=self.registry)
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))
        text = "\n".join("".join(t for _, t in row) for row in frame.lines)
        self.assertLess(text.index("First thought"), text.index("file_read"))
        self.assertLess(text.index("file_read"), text.index("Second 9"))
        self.assertNotIn("fallback", text)

    def test_thought_and_tool_changes_reuse_response_header_body_and_history(self):
        history = tuple(ChatMessage("assistant", f"history {i}", id=f"old{i}") for i in range(100))
        tool = StepSummary("file_read", "running", "tool", "step")
        thought = ChatMessage("reasoning", "Looking at the file", id="a:reasoning", tools=(tool,))
        answer = ChatMessage("assistant", "**Answer**\n\n```py\nx = 1\n```", id="a", status="streaming")
        queued = ChatMessage("user", "Next request", id="queued", status="queued")
        frame = self.prepare((*history, thought, answer, queued))
        prefix = frame.lines.parts[0]
        response = self.cache.items["a"]["frame"]
        parsed, headers, bodies = self.cache.parser.parsed_bytes, self.cache.header_renders, self.cache.body_renders
        for index in range(50):
            thought = replace(thought, text=f"Thinking {index}")
            frame = self.prepare((*history, thought, answer, queued))
            self.assertIs(self.cache.items["a"]["frame"], response)
            self.assertIs(frame.lines.parts[0], prefix)
        self.assertEqual(self.cache.body_renders, bodies + 50)
        self.assertEqual(self.cache.parser.parsed_bytes, parsed)
        self.assertEqual(self.cache.header_renders, headers)
        self.assertEqual(self.cache.prefix_builds, 1)
        bodies = self.cache.body_renders
        thought = replace(thought, tools=(replace(tool, status="completed"),))
        frame = self.prepare((*history, thought, answer, queued))
        self.assertEqual(self.cache.body_renders, bodies)
        self.assertEqual(self.cache.header_renders, headers)
        self.assertEqual(self.cache.tool_renders, 2)
        self.assertIs(self.cache.items["a"]["frame"], response)
        expected = render_messages((*history, thought, answer, queued), 80, self.theme, self.language,
                                   renderers=self.registry, root=Path.cwd())
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))

    def test_elapsed_one_hundred_changes_do_not_parse_or_render_body(self):
        message = ChatMessage("assistant", "**body**\n\n```python\nx = 1\n```", id="a", status="streaming")
        self.prepare((message,))
        parsed = self.cache.parser.parsed_bytes
        for index in range(100):
            self.prepare((replace(message, elapsed_seconds=index / 10),))
        self.assertEqual(self.cache.body_renders, 1)
        self.assertEqual(self.cache.parser.parsed_bytes, parsed)
        self.assertEqual(self.cache.header_renders, 11)  # Unknown, then whole seconds 0–9.

    def test_199_historical_messages_reuse_body_and_prefix(self):
        history = tuple(ChatMessage("assistant", f"**history {i}**", id=str(i)) for i in range(199))
        active = ChatMessage("assistant", "stream", id="active", status="streaming")
        frame = self.prepare((*history, active))
        prefix = frame.lines.parts[0]
        for index in range(100):
            frame = self.prepare((*history, replace(active, text=f"stream {index}")))
            self.assertIs(frame.lines.parts[0], prefix)
        self.assertEqual(self.cache.body_renders, 300)
        self.assertEqual(self.cache.prefix_builds, 1)

    def test_final_frames_match_canonical_rows_anchors_and_objects(self):
        texts = ("[ref][id]\n\n[id]: https://example.org\n\n---",
                 "```python\nx = '한글'\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |",
                 '<hub-code language="python">print(1)</hub-code>\n\n<hub-card title="Card">hello</hub-card>',
                 '<hub-diff>--- a\n+++ b\n-old\n+new</hub-diff>')
        for width in (24, 80, 120):
            for text in texts:
                messages = (ChatMessage("assistant", "previous", id="p"), ChatMessage("user", "질문", id="u"),
                            ChatMessage("assistant", text, id="a", status="streaming"))
                self.prepare(messages, width)
                messages = (*messages[:-1], replace(messages[-1], status="completed"))
                actual = self.prepare(messages, width)
                anchors, objects = [], []
                expected = render_messages(messages, width, self.theme, self.language,
                    anchors=anchors, objects=objects, renderers=self.registry, root=Path.cwd())
                self.assertEqual(tuple(actual.lines), frozen_rows(expected))
                self.assertEqual(tuple(actual.anchors), tuple(anchors))
                self.assertEqual(tuple(actual.objects), tuple(objects))

    def test_image_completion_only_invalidates_its_block(self):
        class Image:
            revision = 0
            def block_revision(self, block, context):
                return self.revision
            def render(self, block, context):
                return [[("", f"image {self.revision}")]]
        image = Image()
        self.registry.register("hub-image", image)
        messages = (ChatMessage("assistant", "past", id="past"),
                    ChatMessage("assistant", 'text\n\n<hub-image src="picture.png"/>', id="active"))
        with patch.object(self.registry, "render", wraps=self.registry.render) as render:
            self.prepare(messages)
            count = render.call_count
            parsed = self.cache.parser.parsed_bytes
            image.revision += 1
            self.prepare(messages)
            self.assertEqual(render.call_count, count + 1)
            self.assertEqual(render.call_args.args[0].kind, "hub-image")
            self.assertEqual(self.cache.parser.parsed_bytes, parsed)

    def test_presentation_reuses_unchanged_history(self):
        cache = MessagePresentationCache()
        history = tuple(ChatMessage("user", str(index), time="2026-10-11T01:00:00+00:00", id=str(index)) for index in range(199))
        active = ChatMessage("assistant", "answer", id="active", elapsed_seconds=0)
        snapshot = HubSnapshot("p", "P", "model", "file", (), "s", (*history, active))
        first = cache.messages(snapshot, self.language)
        for index in range(100):
            result = cache.messages(replace(snapshot, messages=(*history, replace(active, elapsed_seconds=index + 1))), self.language)
            self.assertTrue(all(a is b for a, b in zip(first[:-1], result[:-1])))
        self.assertEqual(cache.transformed, 300)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_displays_inflight_frame_while_newer_frame_is_rendering(self):
        worker = RenderWorker()
        first_gate, second_gate, started = Event(), Event(), Event()
        control = ConversationControl((ChatMessage("assistant", "first", id="a"),), HubTheme())
        control.render_worker = worker
        prepare = control._message_cache.prepare
        def slow(messages, *args, **kwargs):
            started.set()
            (first_gate if messages[0].text == "first" else second_gate).wait(5)
            return prepare(messages, *args, **kwargs)
        app = NS(is_running=True, invalidate=Mock())
        try:
            with set_app(app), patch.object(control._message_cache, "prepare", side_effect=slow):
                control.create_content(80, 10)
                await eventually(started.is_set)
                first = control._requested_key
                control.messages = (ChatMessage("assistant", "second", id="a"),)
                control.create_content(80, 10)
                first_gate.set()
                await eventually(lambda: control._cache_key == first)
                self.assertNotEqual(control._cache_key, control._requested_key)
                self.assertIn("first", "".join(text for line in control._lines for _, text in line))
                second_gate.set()
                await eventually(lambda: control._cache_key == control._requested_key)
        finally:
            first_gate.set()
            second_gate.set()
            worker.close()
            control.renderers.close()

    async def test_progressive_render_publishes_before_newer_work_and_cancel_rejects_old_session(self):
        worker, gate, newer, started = RenderWorker(), Event(), Event(), Event()
        applied = []
        def first():
            started.set()
            gate.wait(5)
            return "first"
        def second():
            newer.wait(5)
            return "second"
        try:
            worker.request("transcript", first, applied.append, str, progressive=True)
            await eventually(started.is_set)
            worker.request("transcript", second, applied.append, str, progressive=True)
            gate.set()
            await eventually(lambda: applied == ["first"])
            worker.cancel("transcript")
            newer.set()
            worker.request("transcript", lambda: "new session", applied.append, str, progressive=True)
            await eventually(lambda: applied[-1] == "new session")
            self.assertEqual(applied, ["first", "new session"])
        finally:
            gate.set()
            newer.set()
            worker.close()

    async def test_slow_component_read_does_not_block_snapshots(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def component_command(*args):
            entered.set()
            await release.wait()
            return ["skill"]
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed = False
        worker._restarting = False
        worker._failure = None
        worker._commands = set()
        worker._snapshot_task = None
        async def snapshot():
            return "new output"
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(),
            component_command=component_command, snapshot=snapshot)
        for arguments in (("p", "skills", "list"), ("p", "rag", 'create doc {"content":"sample"}')):
            entered.clear()
            release.clear()
            command = asyncio.create_task(worker._execute("component_command", arguments))
            try:
                async with asyncio.timeout(1):
                    await entered.wait()
                async with asyncio.timeout(1):
                    self.assertEqual(await worker._read_snapshot(), "new output")
                self.assertFalse(command.done())
            finally:
                release.set()
                await command

    async def test_progress_updates_coalesce_repaints_but_keep_latest_value(self):
        progress = TaskProgress(NS(theme=HubTheme()))
        app = NS(invalidate=Mock())
        try:
            with patch("hub.widget.progress.get_app", return_value=app):
                task = progress.start("Loading", total=100)
                timer = progress._timer
                app.invalidate.reset_mock()
                for index in range(100):
                    progress.update(task, completed=index + 1)
                app.invalidate.assert_not_called()
                self.assertIs(progress._timer, timer)
                self.assertEqual(progress.progress.tasks[0].completed, 100)
                progress.finish(task)
                app.invalidate.assert_called_once()
        finally:
            progress.close()

    async def test_delivery_rechecks_generation_even_after_build_finished(self):
        worker, callbacks, applied = RenderWorker(), [], []
        loop = NS(call_soon_threadsafe=lambda *args: callbacks.append(args))
        try:
            with patch("hub.ui.output.worker.asyncio.get_running_loop", return_value=loop):
                worker.request("transcript", lambda: "A", applied.append, str)
            await eventually(lambda: len(callbacks) == 1)
            with patch("hub.ui.output.worker.asyncio.get_running_loop", return_value=loop):
                worker.request("transcript", lambda: "B", applied.append, str)
            await eventually(lambda: len(callbacks) == 2)
            for callback, *args in callbacks:
                callback(*args)
            self.assertEqual(applied, ["B"])
        finally:
            worker.close()

    async def test_resize_storm_renders_final_width_and_keeps_anchor(self):
        worker = RenderWorker()
        control = ConversationControl((ChatMessage("assistant", "paragraph\n\n" * 80, id="a"),), HubTheme())
        control.create_content(80, 10)
        control.scroll("down")
        position = control.reading_position()
        control.render_worker = worker
        app = NS(is_running=True, invalidate=Mock())
        try:
            with set_app(app):
                for width in range(79, 39, -1):
                    control.create_content(width, 10)
                self.assertEqual(worker.stats.requested, 0)
                await eventually(lambda: control._cache_key == control._requested_key)
            self.assertEqual(worker.stats.requested, 1)
            self.assertEqual(control._cache_key[0], 40)
            self.assertEqual(control.reading_position().message_id, position.message_id)
            self.assertEqual(control.reading_position().offset, position.offset)
        finally:
            worker.close()
            control.renderers.close()

    async def test_running_a_then_latest_d_and_bounded_hundreds_of_updates(self):
        worker, gate, started = RenderWorker(), Event(), Event()
        builds, applied = [], []
        def first():
            builds.append("A")
            started.set()
            gate.wait(5)
            return "A"
        try:
            worker.request("transcript", first, applied.append, str)
            await eventually(started.is_set)
            for value in ("B", "C", *range(300), "D"):
                def build(value=value):
                    builds.append(value)
                    return value
                worker.request("transcript", build, applied.append, str)
            self.assertEqual(worker.stats.pending_peak, 1)
            gate.set()
            await eventually(lambda: applied == ["D"])
            self.assertEqual(builds, ["A", "D"])
        finally:
            gate.set()
            worker.close()

    async def test_fixed_surfaces_share_one_thread_and_final_results_survive(self):
        worker = RenderWorker()
        applied, threads = {}, set()
        try:
            for slot in worker.slots:
                def build(slot=slot):
                    threads.add(get_ident())
                    return slot
                worker.request(slot, build, lambda value: applied.setdefault(value, True), str)
            await eventually(lambda: set(applied) == worker.slots)
            self.assertEqual(len(threads), 1)
            self.assertNotIn(get_ident(), threads)
            self.assertLessEqual(worker.stats.pending_peak, len(worker.slots))
        finally:
            worker.close()

    async def test_failure_uses_fallback_and_close_discards_result(self):
        worker, applied = RenderWorker(), []
        try:
            def fail():
                raise ValueError("broken renderer")
            worker.request("transcript", fail, applied.append, lambda _: "plain text")
            await eventually(lambda: applied == ["plain text"])
            self.assertEqual(worker.stats.failures, 1)
            worker.close()
            worker.request("transcript", lambda: "late", applied.append, str)
            await asyncio.sleep(0)
            self.assertEqual(applied, ["plain text"])
        finally:
            worker.close()

    async def test_input_focus_and_scroll_do_not_wait_for_render_or_snapshot(self):
        gate, started, snapshot_entered = Event(), Event(), asyncio.Event()
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            backend = BackendWorker.__new__(BackendWorker)
            async def snapshot():
                snapshot_entered.set()
                await asyncio.Event().wait()
            backend.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
            backend._output_interval = .01
            backend._interval_changed = asyncio.Event()
            backend.publish = Mock()
            backend.report_error = Mock()
            polling = asyncio.create_task(backend._refresh())
            try:
                await eventually(lambda: app.is_running and view.transcript.control._cache_key is not None)
                original = view.output_renderers.render
                def slow(block, context):
                    started.set()
                    gate.wait(10)
                    return original(block, context)
                with patch.object(view.output_renderers, "render", side_effect=slow):
                    with set_app(app):
                        view.transcript.control.messages = (ChatMessage("assistant", "slow", id="slow"),)
                        app.invalidate()
                    await eventually(started.is_set)
                    await snapshot_entered.wait()
                    pipe.send_text("typing while busy")
                    await eventually(lambda: view.composer.text == "typing while busy")
                    pipe.send_text("\x1b")
                    await eventually(lambda: app.layout.current_control is view._session_control)
                    pipe.send_text("\x1b[C")
                    await eventually(lambda: app.layout.current_control is view.composer.control)
                    with set_app(app):
                        view.transcript.control.scroll("home")
                    self.assertEqual(view.transcript.control.top_line, 0)
                    self.assertFalse(gate.is_set())
                    self.assertFalse(polling.done())
                    gate.set()
                    await eventually(lambda: view.transcript.control._cache_key == view.transcript.control._requested_key)
            finally:
                gate.set()
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()

    async def test_idle_polling_slows_but_dirty_wakes(self):
        reads = []
        async def snapshot():
            reads.append(1)
            return NS(run=None, queued_count=0, sessions=())
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
        worker._output_interval = .01
        worker._interval_changed = asyncio.Event()
        worker.publish, worker.report_error = Mock(), Mock()
        task = asyncio.create_task(worker._refresh())
        try:
            await eventually(lambda: len(reads) == 1)
            await asyncio.sleep(.1)
            self.assertEqual(len(reads), 1)
            worker.runtime.dirty.set()
            await eventually(lambda: len(reads) == 2)
            worker.report_error.assert_not_called()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
