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
from hub.widget.transcript.conversation import render_messages
from hub.widget.transcript.conversation import ConversationControl, ConversationView
from hub.widget.transcript.message_cache import MessageRenderCache, frozen_rows
from hub.widget.feedback.progress import TaskProgress
from hub.ui.presentation import MessagePresentationCache
from hub.model import HubSnapshot
from tests.hub.support import SizedOutput, eventually


class CacheTests(unittest.TestCase):
    def test_attached_instruction_updates_one_bubble_and_keeps_response_cache(self):
        from hub.model import InstructionDisplay
        from prompt_toolkit.formatted_text.utils import fragment_list_width

        first = ChatMessage('user', 'Original request', id='u', author='Account')
        answer = ChatMessage('assistant', 'Unchanged answer', id='a')
        self.prepare((first, answer))
        response = self.cache.items['a'].body
        receipt = InstructionDisplay('i', '**Only read files**', status='pending')
        for state in ('pending', 'applied', 'unapplied'):
            current = replace(first, instructions=(replace(receipt, status=state),))
            frame = self.prepare((current, answer))
            text = '\n'.join(''.join(s for _, s in row) for row in frame.lines)
            self.assertEqual(text.count('Account'), 1)
            self.assertIn('Original request', text)
            self.assertIn('Only read files', text)
            self.assertIn(self.language.status(state), text)
            self.assertIs(self.cache.items['a'].body, response)
            self.assertEqual(tuple(frame.lines), frozen_rows(render_messages(
                (current, answer), 80, self.theme, self.language, renderers=self.registry)))
            self.assertLessEqual(max(map(fragment_list_width, frame.lines)), 80)

    def test_render_resources_are_reused_and_bounded_across_widths(self):
        from hub.ui.output.markdown import RenderResources, make_console, _segment_style
        from rich.style import Style
        resources = RenderResources()
        with patch('hub.ui.output.markdown.make_console', wraps=make_console) as make:
            first = resources.console(80, self.theme)
            for _ in range(20):
                self.assertIs(resources.console(80, self.theme), first)
            self.assertEqual(make.call_count, 1)
            for width in range(90, 100):
                resources.console(width, self.theme)
            self.assertEqual(len(resources.consoles), resources.CONSOLES)
        style = Style(color=self.theme.accent, bold=True, bgcolor=self.theme.code_background)
        self.assertEqual(resources.style(style, self.theme.code_background),
                         _segment_style(style, self.theme.code_background))
        with patch('hub.ui.output.markdown._segment_style', wraps=_segment_style) as convert:
            resources.style(style, self.theme.code_background)
            convert.assert_not_called()

    def test_streaming_change_skips_prefix_and_unchanged_trace(self):
        history = tuple(ChatMessage('assistant', 'History', id=str(i)) for i in range(80))
        trace = tuple(TraceItem(str(i), TraceItem.TOOL, 'file_read', status='completed') for i in range(100))
        active = ChatMessage('reasoning', '', id='active:reasoning', trace=trace)
        messages = (*history, active)
        self.prepare(messages)
        examined, traces = self.cache.examined_messages, self.cache.examined_traces
        for second in range(1, 5):
            active = replace(active, elapsed_seconds=second)
            self.prepare((*history, active))
        self.assertEqual(self.cache.examined_messages - examined, 4)
        self.assertEqual(self.cache.examined_traces, traces)
        trace = (*trace[:-1], replace(trace[-1], status='running'))
        active = replace(active, trace=trace)
        frame = self.prepare((*history, active))
        self.assertEqual(self.cache.examined_traces - traces, 1)
        expected = render_messages((*history, active), 80, self.theme, self.language,
                                   renderers=self.registry)
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))
        # Removing/reordering rows invalidates from the changed position.
        changed = (*history[2:], history[0], active)
        frame = self.prepare(changed)
        self.assertEqual(tuple(frame.lines), frozen_rows(render_messages(
            changed, 80, self.theme, self.language, renderers=self.registry)))

    def setUp(self):
        self.cache = MessageRenderCache()
        self.registry = RendererRegistry()
        self.theme, self.language = HubTheme(), Language()
        self.addCleanup(self.registry.close)

    def prepare(self, messages, width=80):
        return self.cache.prepare(messages, width, self.theme, self.language,
            render=render_messages, renderers=self.registry, root=Path.cwd(), invalidate=lambda: None)

    def test_streaming_suffix_does_not_measure_unchanged_block_rows(self):
        from prompt_toolkit.formatted_text.utils import fragment_list_width
        prefix = '```python\n' + "print('한글')\n" * 1000 + '```\n\n'
        message = ChatMessage('assistant', prefix, id='a', status='streaming')
        self.prepare((message,))
        with patch('hub.widget.transcript.message_cache.fragment_list_width',
                   wraps=fragment_list_width) as measure:
            for index in range(5):
                current = replace(message, text=prefix + f'**New suffix {index}**')
                frame = self.prepare((current,))
                self.assertEqual(frame.max_width, max(map(fragment_list_width, frame.lines)))
            self.assertLess(measure.call_count, 80)  # Not the 1,000 prior rows per delta.
        expected = render_messages((current,), 80, self.theme, self.language, renderers=self.registry)
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))
        # A resize and final publication retain exact canonical geometry.
        for width in (24, 120):
            final = replace(current, status='completed')
            frame = self.prepare((final,), width)
            expected = render_messages((final,), width, self.theme, self.language, renderers=self.registry)
            self.assertEqual(tuple(frame.lines), frozen_rows(expected))
            self.assertEqual(frame.max_width, max(map(fragment_list_width, frame.lines)))

    def test_trace_changes_render_only_the_changed_item(self):
        trace = (TraceItem("c1", "reasoning", "**First thought**"),
                 TraceItem("t1", "tool", "file_read", "completed"),
                 TraceItem("c2", "reasoning", "Second thought"))
        thought = ChatMessage("reasoning", "combined fallback", id="a:reasoning", trace=trace)
        answer = ChatMessage("assistant", "Answer", id="a", status="streaming")
        self.prepare((thought, answer))
        response = self.cache.items["a"].frame
        previous = self.cache.items["a:reasoning"].trace_cache
        bodies, headers = self.cache.body_renders, self.cache.header_renders
        for index in range(10):
            thought = replace(thought, text=f"fallback {index}", trace=(*trace[:-1], replace(trace[-1], text=f"Second {index}")))
            frame = self.prepare((thought, answer))
            self.assertIs(self.cache.items["a"].frame, response)
            self.assertIs(self.cache.items["a:reasoning"].trace_cache[trace[0]], previous[trace[0]])
        self.assertEqual(self.cache.trace_renders, 13)
        self.assertEqual(self.cache.body_renders, bodies)
        self.assertEqual(self.cache.header_renders, headers)
        expected = render_messages((thought, answer), 80, self.theme, self.language, renderers=self.registry)
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))
        text = "\n".join("".join(t for _, t in row) for row in frame.lines)
        self.assertLess(text.index("First thought"), text.index("file_read"))
        self.assertLess(text.index("file_read"), text.index("Second 9"))
        self.assertNotIn("fallback", text)

    def test_graph_progress_reuses_other_agent_response_and_fits_output_width(self):
        import json
        from llm.components.workflows import WorkflowGraph
        from prompt_toolkit.utils import get_cwidth
        graph = WorkflowGraph(entry='working').node('working', 'hold').node('done', 'end').connect('working', 'done')
        trace = TraceItem('graph', TraceItem.WORKFLOW, json.dumps(graph.to_dict()),
                          workflow_states=(('working', 'running'),))
        thought = ChatMessage('reasoning', '', id='graph:reasoning', author='Planner', trace=(trace,))
        answer = ChatMessage('assistant', 'Another agent answer', id='other', author='Reviewer')
        self.prepare((thought, answer), width=42)
        response = self.cache.items['other'].frame
        bodies, headers, traces = self.cache.body_renders, self.cache.header_renders, self.cache.trace_renders
        thought = replace(thought, trace=(replace(trace, workflow_states=(('working', 'completed'), ('done', 'running'))),))
        frame = self.prepare((thought, answer), width=42)
        self.assertIs(self.cache.items['other'].frame, response)
        self.assertEqual((self.cache.body_renders, self.cache.header_renders), (bodies, headers))
        self.assertEqual(self.cache.trace_renders, traces + 1)
        rows = [''.join(text for _, text in row) for row in frame.lines]
        self.assertTrue(all(get_cwidth(row) <= 42 for row in rows))
        self.assertEqual(sum('┌' in row and '┐' in row for row in rows), 2)

    def test_thought_and_tool_changes_reuse_response_header_body_and_history(self):
        history = tuple(ChatMessage("assistant", f"history {i}", id=f"old{i}") for i in range(100))
        tool = StepSummary("file_read", "running", "tool", "step")
        thought = ChatMessage("reasoning", "Looking at the file", id="a:reasoning", tools=(tool,))
        answer = ChatMessage("assistant", "**Answer**\n\n```py\nx = 1\n```", id="a", status="streaming")
        queued = ChatMessage("user", "Next request", id="queued", status="queued")
        frame = self.prepare((*history, thought, answer, queued))
        prefix = frame.lines.parts[0]
        response = self.cache.items["a"].frame
        parsed, headers, bodies = self.cache.parser.parsed_bytes, self.cache.header_renders, self.cache.body_renders
        for index in range(50):
            thought = replace(thought, text=f"Thinking {index}")
            frame = self.prepare((*history, thought, answer, queued))
            self.assertIs(self.cache.items["a"].frame, response)
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
        self.assertIs(self.cache.items["a"].frame, response)
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

    def test_completed_reasoning_is_in_cached_prefix_not_live_tail(self):
        history = tuple(item for i in range(80) for item in (
            ChatMessage("reasoning", f"Old thought {i}", id=f"old{i}:reasoning"),
            ChatMessage("assistant", f"Old answer {i}", id=f"old{i}")))
        thought = ChatMessage("reasoning", "Current thought", id="active:reasoning")
        answer = ChatMessage("assistant", "Current answer", id="active", status="streaming")
        frame = self.prepare((*history, thought, answer))
        prefix = frame.lines.parts[0]
        text = '\n'.join(''.join(fragment[1] for fragment in row) for row in prefix)
        self.assertIn("Old thought 0", text)
        self.assertIn("Old answer 79", text)
        for index in range(20):
            thought = replace(thought, text=f"Current thought {index}")
            frame = self.prepare((*history, thought, answer))
            self.assertIs(frame.lines.parts[0], prefix)
        self.assertEqual(self.cache.prefix_builds, 1)
        expected = render_messages((*history, thought, answer), 80, self.theme,
                                   self.language, renderers=self.registry)
        self.assertEqual(tuple(frame.lines), frozen_rows(expected))

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
    async def test_long_popup_yields_to_stream_and_cancellation_closes_work(self):
        from hub.ui.output.work import RenderWork
        worker, entered, release, finish, closed = RenderWorker(), Event(), Event(), Event(), Event()
        applied = []
        def rows():
            try:
                entered.set()
                release.wait(5)
                yield
                finish.wait(5)
                yield
                return 'popup'
            finally:
                closed.set()
        try:
            with patch.object(RenderWork, 'SLICE_SECONDS', 0):
                worker.request('popup', lambda: RenderWork(rows()), applied.append, str)
                await eventually(entered.is_set)
                worker.request('transcript', lambda: 'stream', applied.append, str, progressive=True)
                release.set()
                await eventually(lambda: 'stream' in applied)
                self.assertNotIn('popup', applied)
                worker.cancel('popup')
                finish.set()
                await eventually(closed.is_set)
                await asyncio.sleep(.02)
                self.assertEqual(applied, ['stream'])
                self.assertLessEqual(worker.stats.pending_peak, len(worker.slots))
                self.assertGreater(worker.stats.yielded, 0)
        finally:
            release.set(); finish.set(); worker.close()

    async def test_sliced_markdown_popup_matches_canonical_rich_rows(self):
        from hub.widget.output.text_viewport import TextViewport
        from hub.ui.output.markdown import HubMarkdown, make_console, _segment_style
        from hub.ui.output.work import RenderWork
        theme = HubTheme()
        text = '# Heading\n\n한글 **bold** [link][ref]\n\n' + ('```python\nprint(1)\n```\n\n' * 160) + '[ref]: https://example.test\n'
        control = TextViewport(text, markdown=True, theme=lambda: theme)
        for width in (23, 80):
            _, build = control.render_request(width)
            observed = RenderWork(control._prepare_rows_iter(build())).finish()
            console = make_console(width, theme)
            expected = tuple(tuple((_segment_style(s.style, theme.code_background), s.text) for s in row)
                for row in console.render_lines(HubMarkdown(text, theme), console.options, pad=False))
            self.assertEqual(len(observed.fragments), len(expected))
            for row, (actual, canonical) in enumerate(zip(observed.fragments, expected)):
                self.assertEqual(actual, canonical, f'Rich row {row} at width {width}')

    async def test_sliced_search_preserves_cross_line_matches(self):
        from hub.widget.output.search_index import index_lines, index_lines_iter
        from hub.ui.output.work import RenderWork
        lines = [('', 'ignored')] * 140
        rows = [tuple([line]) for line in lines] + [(('', '한글 Alpha'),), (('', '  BETA xyz'),)]
        for query in ('alpha beta', '한글', 'not present'):
            self.assertEqual(RenderWork(index_lines_iter(rows, query)).finish(), index_lines(rows, query))
        self.assertEqual(len(index_lines(rows, 'Alpha beta').hits), 1)

    async def test_replacing_history_cancels_pending_resize_before_next_frame(self):
        view = ConversationView((ChatMessage("assistant", "old", id="old"),), HubTheme())
        control, worker = view.control, RenderWorker()
        control.create_content(80, 10)
        control.render_worker = worker
        app = NS(is_running=True, invalidate=Mock())
        try:
            with set_app(app):
                control.create_content(70, 10)
                timer = control._resize_timer
                self.assertIsNotNone(timer)
                view.set_messages((ChatMessage("assistant", "restored", id="new"),))
                self.assertTrue(timer.cancelled())
                self.assertIsNone(control._resize_timer)
                self.assertEqual(worker.stats.requested, 0)
                control.create_content(70, 10)
                await eventually(lambda: control._cache_key == control._requested_key)
                self.assertEqual(worker.stats.requested, 1)
                text = "\n".join("".join(fragment[1] for fragment in row) for row in control._lines)
                self.assertIn("restored", text)
                self.assertNotIn("old", text)
        finally:
            worker.close()
            control.renderers.close()

    async def test_conversation_displays_inflight_frame_while_newer_frame_is_rendering(self):
        worker = RenderWorker()
        first_gate, second_gate, started = Event(), Event(), Event()
        first_text, second_text = "first\n\n" * 80, "second\n\n" * 120
        control = ConversationControl((ChatMessage("assistant", first_text, id="a"),), HubTheme())
        control.render_worker = worker
        control.follow_tail = True
        prepare = control._message_cache.prepare
        def slow(messages, *args, **kwargs):
            started.set()
            (first_gate if messages[0].text == first_text else second_gate).wait(5)
            return prepare(messages, *args, **kwargs)
        app = NS(is_running=True, invalidate=Mock())
        try:
            with set_app(app), patch.object(control._message_cache, "prepare", side_effect=slow):
                control.create_content(80, 30)
                await eventually(started.is_set)
                first = control._requested_key
                control.messages = (ChatMessage("assistant", second_text, id="a"),)
                control.create_content(80, 30)
                first_gate.set()
                await eventually(lambda: control._cache_key == first)
                self.assertNotEqual(control._cache_key, control._requested_key)
                self.assertIn("first", "".join(text for line in control._lines for _, text in line))
                self.assertEqual(control.top_line, len(control._lines) - 30)
                second_gate.set()
                await eventually(lambda: control._cache_key == control._requested_key)
                control.create_content(80, 8)
                self.assertEqual(control.top_line, len(control._lines) - 8)
                control.scroll("pageup")
                position = control.reading_position()
                control.create_content(80, 6)
                self.assertEqual(control.reading_position(), position)
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
        async def manage_records(*args):
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
            manage_records=manage_records, snapshot=snapshot)
        for arguments in (("p", "skills", "list"), ("p", "rag", 'create doc {"content":"sample"}')):
            entered.clear()
            release.clear()
            command = asyncio.create_task(worker._execute("manage_records", arguments))
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
        progress = TaskProgress(NS(theme=HubTheme(), _dialog=None))
        app = NS(invalidate=Mock())
        try:
            with patch("hub.widget.feedback.progress.get_app", return_value=app):
                task = progress.start("Loading", total=100)
                timer = progress._timer
                app.invalidate.reset_mock()
                for index in range(100):
                    progress.update(task, completed=index + 1)
                progress.update(task, completed=100, description="Indexing", indeterminate=True)
                app.invalidate.assert_not_called()
                self.assertIs(progress._timer, timer)
                self.assertEqual(progress.progress.tasks[0].completed, 100)
                self.assertEqual(progress.progress.tasks[0].description, "Indexing")
                self.assertIsNone(progress.progress.tasks[0].total)
                self.assertFalse(progress.progress.tasks[0].finished)
                self.assertEqual(progress.progress.tasks[0].fields["percent"], "")
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
            backend._restarting = False
            async def snapshot():
                snapshot_entered.set()
                await asyncio.Event().wait()
            backend.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
            backend._output_interval = .01
            backend._visible = True
            backend._interval_changed = asyncio.Event()
            backend.publish = Mock()
            backend.report_error = Mock()
            polling = asyncio.create_task(backend._refresh())
            try:
                await eventually(lambda: app.is_running and view.output.transcript.control._cache_key is not None)
                original = view.output.renderers.render
                def slow(block, context):
                    started.set()
                    gate.wait(10)
                    return original(block, context)
                with patch.object(view.output.renderers, "render", side_effect=slow):
                    with set_app(app):
                        view.output.transcript.control.messages = (ChatMessage("assistant", "slow", id="slow"),)
                        app.invalidate()
                    await eventually(started.is_set)
                    await asyncio.wait_for(snapshot_entered.wait(), 5)
                    pipe.send_text("typing while busy")
                    await eventually(lambda: view.composer.text == "typing while busy")
                    pipe.send_text("\x1b")
                    await eventually(lambda: app.layout.current_control is view._session_control)
                    pipe.send_text("\x1b[C")
                    await eventually(lambda: app.layout.current_control is view.composer.control)
                    with set_app(app):
                        view.output.transcript.control.scroll("home")
                    self.assertEqual(view.output.transcript.control.top_line, 0)
                    self.assertFalse(gate.is_set())
                    self.assertFalse(polling.done())
                    gate.set()
                    await eventually(lambda: view.output.transcript.control._cache_key == view.output.transcript.control._requested_key)
            finally:
                gate.set()
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
                app.exit()
                await task
                view.close()
                view.progress.close()

    async def test_idle_polling_slows_but_dirty_wakes(self):
        reads = []
        async def snapshot():
            reads.append(1)
            return NS(run=None, queued_count=0, sessions=())
        worker = BackendWorker.__new__(BackendWorker)
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot)
        worker._restarting = False
        worker._output_interval = .01
        worker._visible = True
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
