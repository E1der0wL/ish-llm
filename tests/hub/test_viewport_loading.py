"""Visible-neighborhood work and visited caches, without speculative loading."""

import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hub.backend.observability.snapshot import SnapshotReader
from hub.backend.worker import BackendWorker
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage
from hub.ui.output import RendererRegistry
from hub.widget.transcript.conversation import ConversationControl, render_messages
from hub.widget.transcript.message_cache import MessageRenderCache
from llm.core.models import Message


class ViewportLoadingTests(unittest.IsolatedAsyncioTestCase):
    def test_session_selection_sends_the_restored_message_not_widget_anchor(self):
        from hub.config.view_records import ReadingPosition
        from hub.ui.live import LiveController

        controller = LiveController.__new__(LiveController)
        controller.view = NS(output=NS(reading_position=lambda: ReadingPosition('a0:block:40', 2, 100)))
        controller._call = Mock()
        controller.select('s0')
        controller._call.assert_called_once_with('select', 's0', 'a0')

    def test_rich_only_renders_neighborhood_and_reuses_visited_frames(self):
        cache, registry = MessageRenderCache(), RendererRegistry()
        self.addCleanup(registry.close)
        theme, language = HubTheme(), Language()
        messages = tuple(ChatMessage('assistant', f'```python\nprint({i})\n```',
                                     id=f'a{i}', status='completed') for i in range(200))
        def render(values, visible):
            return cache.prepare(values, 80, theme, language, render=render_messages,
                renderers=registry, root=Path.cwd(), invalidate=lambda: None, visible=visible)
        frame = render(messages, (190, 200))
        self.assertEqual(cache.body_renders, 10)
        self.assertEqual(len(frame.objects), 200)  # The tag catalog keeps lossless raw objects.
        self.assertEqual(frame.objects[0].lines, ())
        self.assertIn('print(0)', frame.objects[0].raw)
        render(messages, (0, 10))
        self.assertEqual(cache.body_renders, 20)
        other = tuple(ChatMessage('assistant', f'other {i}', id=f'b{i}') for i in range(100))
        render(other, (90, 100))
        count = cache.body_renders
        frame = render(messages, (190, 200))
        self.assertEqual(cache.body_renders, count)
        self.assertTrue(frame.objects[0].lines)

    def test_offscreen_edit_is_rendered_fresh_when_visited(self):
        from dataclasses import replace
        cache, registry = MessageRenderCache(), RendererRegistry()
        self.addCleanup(registry.close)
        theme, language = HubTheme(), Language()
        messages = tuple(ChatMessage('assistant', f'old {i}', id=f'a{i}') for i in range(40))
        def render(values, visible):
            return cache.prepare(values, 80, theme, language, render=render_messages,
                renderers=registry, root=Path.cwd(), invalidate=lambda: None, visible=visible)
        render(messages, (0, 5))
        changed = (replace(messages[0], text='NEW CONTENT'), *messages[1:])
        render(changed, (35, 40))
        render(changed, (0, 5))
        body = cache.items['a0'].body.lines
        self.assertIn('NEW CONTENT', ''.join(text for row in body for _, text in row))

    def test_home_scroll_renders_new_neighborhood_without_full_history(self):
        control = ConversationControl(tuple(ChatMessage('assistant', f'Answer {i}', id=f'a{i}')
                                            for i in range(200)), HubTheme())
        self.addCleanup(control.renderers.close)
        control.follow_tail = True
        control.create_content(80, 12)
        self.assertLessEqual(control._message_cache.body_renders, 24)
        control.follow_tail = False
        control.scroll_to(0)
        control.create_content(80, 12)
        self.assertLess(control._message_cache.body_renders, 60)
        self.assertIsNotNone(control._message_cache.items['a0'].body)

    def test_offscreen_search_and_cache_eviction_keep_content_accessible(self):
        control = ConversationControl(tuple(ChatMessage('assistant', f'Answer {i}', id=f'a{i}')
                                            for i in range(200)), HubTheme())
        self.addCleanup(control.renderers.close)
        control.follow_tail = True
        control.create_content(80, 12)
        control.set_search('Answer 17')
        control.create_content(80, 12)
        self.assertTrue(control.search_hits)
        self.assertIsNotNone(control._message_cache.items['a17'].body)
        control.set_search('')
        for index in range(6):
            control.messages = (ChatMessage('assistant', f'Other {index}', id=f'b{index}'),)
            control.create_content(80, 12)
        self.assertEqual(len(control._message_cache._contexts), 3)

    def test_offscreen_tag_restores_exact_anchor_after_reflow(self):
        from hub.config.view_records import ReadingPosition

        control = ConversationControl(tuple(ChatMessage('assistant',
            f'Title {i}\n\n```python\n' + f'print({i})\n' * (i % 8 + 1) + '```', id=f'a{i}')
            for i in range(200)), HubTheme())
        self.addCleanup(control.renderers.close)
        control.follow_tail = True
        control.create_content(60, 12)
        target = next(item for item in control.objects if item.message_id == 'a100')
        self.assertFalse(target.lines)
        control.restore_position(ReadingPosition(target.id, 0, target.start_line))
        control.create_content(60, 12)
        restored = next(item for item in control.objects if item.id == target.id)
        self.assertTrue(restored.lines)
        self.assertEqual(control.top_line, control.anchor_positions()[target.id])
        self.assertLess(control._message_cache.body_renders, 60)
        control.create_content(40, 12)
        self.assertEqual(control.reading_position().message_id, target.id)

    async def test_only_visited_sessions_cached_and_inactive_changes_reconciled(self):
        from hub.backend.observability.reads import ReadKind
        from llm.core.results import EngineDelta, OutputVisibility
        from llm.engines.events import EngineEvent, EngineEventType

        reader, sessions = SnapshotReader(), {}
        for index in range(6):
            sid = f's{index}'
            sessions[sid] = NS(
                aget_data=AsyncMock(return_value=NS(id=sid, title=sid, agent_ids=[], metadata={})),
                aconversation=AsyncMock(return_value=[Message(id=f'a{index}', role='user', content=sid,
                                                             status='completed')]),
                run=NS(astatus=AsyncMock(return_value=NS(status='idle', queued_count=0, active_runs=[])),
                       alist=AsyncMock(return_value=[])))
        project = NS(aget_data=AsyncMock(return_value=NS(id='p', title='P', config={},
                     components=[], conversation_storage='file')), paths=NS(root='/workspace'),
                     agents=NS(alist=AsyncMock(return_value={})))
        async def read(sid):
            return (await reader.read(project, sessions, sid,
                config=NS(agent_id='assistant', file_root=None, user_profile=NS(display_name='A')),
                engines=NS(names=lambda: ('loop',), get=lambda _: NS()), title_errors={}))[0]
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            first = await read('s0')
            reader.focus_history('s0', 'a0')
            await read('s0')
            await read('s1')
            restored = await read('s0')
            self.assertEqual(restored.messages, first.messages)
            sessions['s0'].aconversation.assert_awaited_once()
            sessions['s2'].aconversation.assert_not_awaited()
            await read('s1')
            sessions['s0'].aconversation.return_value = [
                Message(id='a0', role='user', content='updated', status='completed')]
            reader.observe_engine(NS(id='r0', session_id='s0'),
                EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta(
                    output_id='user', text='updated', visibility=OutputVisibility.USER)))
            self.assertEqual((await read('s0')).messages[0].text, 'updated')
            self.assertEqual(sessions['s0'].aconversation.await_count, 2)
            reader.invalidate_dropped_events()
            self.assertEqual(reader.projections['s0'].focus_id, 'a0')
            await read('s0')
            self.assertEqual(sessions['s0'].aconversation.await_count, 3)
            for index in range(1, 6):
                await read(f's{index}')
            self.assertEqual(len(reader.projections), 4)
            self.assertNotIn('s0', reader.projections)
            self.assertNotIn((ReadKind.CONVERSATION, 's0'), reader.reads.entries)
            del sessions['s2']
            await read('s5')
            self.assertNotIn('s2', reader.projections)
            reader.clear()
            self.assertFalse(reader.projections)

    async def test_historical_run_details_load_only_near_requested_messages(self):
        reader = SnapshotReader()
        records = [Message(id=f'a{i}', role='assistant', content=f'Answer {i}',
                           status='completed', run_id=f'r{i}') for i in range(200)]
        async def load(identifier):
            record = NS(id=identifier, status='completed', engine='loop', started_at=None,
                        ended_at=None, metadata={'completions': []})
            return NS(aget_data=AsyncMock(return_value=record))
        session = NS(run=NS(aload=AsyncMock(side_effect=load)))
        last = {record.id for record in records[-3:]}
        result = await reader.messages(session, records, detail_ids=last)
        self.assertEqual(len(result), 200)
        self.assertEqual(session.run.aload.await_count, 3)
        await reader.messages(session, records, detail_ids=last)
        self.assertEqual(session.run.aload.await_count, 3)
        await reader.messages(session, records, detail_ids={record.id for record in records[:3]})
        self.assertEqual(session.run.aload.await_count, 6)
        await reader.messages(session, records, detail_ids=last)
        self.assertEqual(session.run.aload.await_count, 6)

    async def test_session_selection_preempts_old_display_read(self):
        worker = BackendWorker.__new__(BackendWorker)
        worker._initialized = asyncio.Event()
        worker._initialized.set()
        worker._closed = worker._restarting = False
        worker._failure = None
        worker._commands = set()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def snapshot():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        worker.runtime = NS(lock=asyncio.Lock(), dirty=asyncio.Event(), snapshot=snapshot,
                            select=AsyncMock())
        worker._snapshot_task = asyncio.create_task(worker._read_snapshot())
        await started.wait()
        await asyncio.wait_for(worker._execute('select', ('new',)), 1)
        self.assertTrue(cancelled.is_set())
        worker.runtime.select.assert_awaited_once_with('new')
        await asyncio.gather(worker._snapshot_task, return_exceptions=True)
