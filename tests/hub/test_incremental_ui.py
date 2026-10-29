"""Bounded UI work without changing drafts, continuation order or observations."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hub.backend.execution.turns import TurnIndex
from hub.backend.observability.reads import ObservationReads
from hub.backend.observability.snapshot import SnapshotReader
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage
from hub.ui.output import RendererRegistry
from hub.widget.forms import settings
from hub.widget.transcript.conversation import render_messages
from hub.widget.transcript.message_cache import MessageRenderCache, frozen_rows
from llm.core.models import Message
from tests.hub import test_observation_reads as observations


class LazyFieldTests(unittest.TestCase):
    def test_validation_and_draft_values_do_not_create_hidden_widgets(self):
        schema = {'type': 'object', 'properties': {
            'visible': {'type': 'string'}, 'hidden': {'type': 'integer'},
            'locked': {'type': 'integer', 'x-host-override': True}}}
        original = {'visible': 'before', 'hidden': 2, 'locked': 9,
                    'extension': {'unknown': None}}
        with patch.object(settings, 'TextArea', wraps=settings.TextArea) as inputs:
            form = settings.SchemaForm(schema, original, Language('en'))
            form.fields[('visible',)].text = 'draft'
            self.assertEqual(form.values(), {**original, 'visible': 'draft'})
            inputs.assert_not_called()
            field = form.fields[('visible',)]
            widget = field.input
            self.assertEqual(widget.text, 'draft')
            field.input.text = 'edited'
            field.finish()
            self.assertIs(field.input, widget)
            self.assertEqual(form.values(), {**original, 'visible': 'edited'})
            self.assertEqual(inputs.call_count, 1)
            form.fields[('hidden',)].text = 'invalid integer'
            with self.assertRaisesRegex(ValueError, 'hidden'):
                form.values()
            self.assertEqual(inputs.call_count, 1)
        self.assertEqual(original['visible'], 'before')

    def test_choice_draft_survives_delayed_creation_and_empty_form_is_visible(self):
        schema = {'type': 'string', settings.ChoiceField.SCHEMA_KEY: {'a': 'A', 'b': 'B'}}
        field = settings.ChoiceField('choice', schema, 'a', Language('en'))
        field.text = 'B'
        self.assertEqual(field.value(), 'b')
        self.assertEqual(field.input.text, 'B')
        self.assertTrue(field.input.buffer.read_only())
        self.assertTrue(field.input.window.always_hide_cursor())
        self.assertTrue(settings.SchemaForm(False, {}, Language('en')).container.children)


class IncrementalProjectionTests(unittest.IsolatedAsyncioTestCase):
    fixture = observations.ObservationReadTests.fixture
    read = observations.ObservationReadTests.read

    async def test_changed_run_rebuilds_only_its_rows_and_matches_full_projection(self):
        self.fixture()
        history = [Message(id=f'u{i}', role='user', content=f'question {i}', status='completed')
                   for i in range(500)]
        records = [*history, self.message]
        first = await self.reader.messages(None, records, latest=self.run)
        with patch('hub.backend.observability.snapshot.ChatMessage', wraps=ChatMessage) as rows:
            for _ in range(20):
                self.reader.projection.changed(self.run.id)
                current = await self.reader.messages(None, records, latest=self.run)
                self.assertTrue(all(a is b for a, b in zip(first[:-1], current[:-1])))
            self.assertEqual(rows.call_count, 20)
        expected = await SnapshotReader().messages(None, records, latest=self.run)
        self.assertEqual(current, expected)
        canonical = TurnIndex(records).display([
            row for record in records for row in self.reader.projection.parts[record.id]])
        self.assertEqual(current, canonical)

    async def test_profile_change_new_history_and_deletion_rebuild_correct_rows(self):
        self.fixture()
        records = [Message(id='u', role='user', content='question', status='completed'), self.message]
        first = await self.reader.messages(None, records, 'First', latest=self.run)
        changed = await self.reader.messages(None, records, 'Second', latest=self.run)
        self.assertEqual(changed[0].author, 'Second')
        self.assertIsNot(first[0], changed[0])
        records = [replace(records[0], content='edited'), replace(self.message, content='new answer')]
        updated = await self.reader.messages(None, records, 'Second', latest=self.run)
        self.assertEqual([m.text for m in updated], ['edited', 'new answer'])
        remaining = await self.reader.messages(None, records[1:], 'Second', latest=self.run)
        self.assertEqual([m.id for m in remaining], ['a'])
        self.assertNotIn('u', self.reader.projection.parts)
        self.assertNotIn('u', self.reader.projection.displayed)

    async def test_dropped_event_during_projection_keeps_history_and_forces_refresh(self):
        self.fixture()
        records = [Message(id='u', role='user', content='keep history', status='completed'), self.message]
        await self.reader.messages(None, records, latest=self.run)
        original = self.reader.run_display
        async def interrupted(*args, **kwargs):
            self.reader.invalidate_dropped_events()
            await asyncio.sleep(0)
            return await original(*args, **kwargs)
        with patch.object(self.reader, 'run_display', side_effect=interrupted):
            result = await self.reader.messages(None, records, latest=self.run)
        self.assertEqual([m.id for m in result], ['u', 'a'])
        self.assertTrue(self.reader.projection.full_refresh)
        self.assertEqual(await self.reader.messages(None, records, latest=self.run), result)

    async def test_failed_historical_read_cannot_cache_an_incomplete_index(self):
        self.fixture()
        records = [Message(id='u', role='user', content='past question', status='completed'),
                   Message(id='old', role='assistant', content='past answer', status='completed',
                           run_id='older'), self.message]
        original = self.reader.run_display
        failures = 1
        async def read(session, run_id, **kwargs):
            nonlocal failures
            if run_id == 'older':
                if failures:
                    failures -= 1
                    raise OSError('temporary historical read failure')
                return '', None
            return await original(session, run_id, **kwargs)
        with patch.object(self.reader, 'run_display', side_effect=read):
            with self.assertRaisesRegex(OSError, 'historical read'):
                await self.reader.messages(None, records, latest=self.run)
            self.assertTrue(self.reader.projection.full_refresh)
            result = await self.reader.messages(None, records, latest=self.run)
        self.assertEqual([m.id for m in result], ['u', 'old', 'a'])
        self.assertEqual([m.text for m in result], ['past question', 'past answer', 'hello'])

    async def test_summaries_are_reused_and_one_session_event_replaces_only_its_summary(self):
        self.fixture()
        other = NS(aget_data=AsyncMock(return_value=NS(
            id='other', title='Other', agent_ids=[], metadata={})),
            run=NS(astatus=AsyncMock(return_value=NS(status='idle', queued_count=0, active_runs=[])),
                   alist=AsyncMock(return_value=[])))
        async def read():
            return (await self.reader.read(self.project, {'s': self.session, 'other': other}, '',
                config=self.config, engines=self.engines, title_errors={}))[0]
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            first = await read()
            for _ in range(30):
                current = await read()
                self.assertTrue(all(a is b for a, b in zip(first.sessions, current.sessions)))
            self.reader.observe_run(self.run)
            changed = await read()
            self.assertIsNot(first.sessions[0], changed.sessions[0])
            self.assertIs(first.sessions[1], changed.sessions[1])
            self.assertEqual(self.session.aget_data.await_count, 2)
            other.aget_data.assert_awaited_once()

    async def test_background_reads_expire_in_phases_and_event_does_not_wait(self):
        cache = ObservationReads()
        loaders = {str(i): AsyncMock(return_value=i) for i in range(100)}
        ages = {i: cache.background_age(i) for i in loaders}
        self.assertGreaterEqual(min(ages.values()), 7.5)
        self.assertLessEqual(max(ages.values()), 10)
        self.assertGreater(len(set(ages.values())), 20)
        async def read():
            for i, load in loaders.items():
                await cache.read(('session', i), load, max_age=ages[i])
        with patch('hub.backend.observability.reads.monotonic', return_value=100):
            await read()
        with patch('hub.backend.observability.reads.monotonic', return_value=108.5):
            await read()
            count = sum(load.await_count > 1 for load in loaders.values())
            self.assertGreater(count, 0)
            self.assertLess(count, 100)
            fresh = next(i for i, load in loaders.items() if load.await_count == 1)
            cache.discard('session', fresh)
            await read()
            self.assertEqual(loaders[fresh].await_count, 2)


class IncrementalRenderTests(unittest.TestCase):
    def setUp(self):
        self.cache, self.registry = MessageRenderCache(), RendererRegistry()
        self.theme, self.language = HubTheme(), Language('en')
        self.addCleanup(self.registry.close)

    def prepare(self, messages, width=80):
        return self.cache.prepare(messages, width, self.theme, self.language,
            render=render_messages, renderers=self.registry, root=Path.cwd(), invalidate=lambda: None)

    def test_unchanged_history_skips_dependency_walk_and_preserves_anchors_after_resize(self):
        history = tuple(ChatMessage('assistant', f'```py\nx = {i}\n```', id=str(i)) for i in range(100))
        active = ChatMessage('assistant', 'answer', id='a', status='streaming')
        self.prepare((*history, active))
        with patch.object(self.registry, 'block_revision', wraps=self.registry.block_revision) as revisions:
            self.prepare((*history, replace(active, text='changed')))
            self.assertTrue(revisions.call_count < 10)
        for width in (30, 80):
            messages = (*history[5:], replace(active, text='final', status='completed'))
            frame = self.prepare(messages, width)
            anchors, objects = [], []
            expected = render_messages(messages, width, self.theme, self.language,
                renderers=self.registry, root=Path.cwd(), anchors=anchors, objects=objects)
            self.assertEqual(tuple(frame.lines), frozen_rows(expected))
            self.assertEqual(tuple(frame.anchors), tuple(anchors))
            self.assertEqual(tuple(frame.objects), tuple(objects))

    def test_block_dependency_refreshes_when_global_renderer_revision_does_not_change(self):
        class Dynamic:
            content = 'before'
            def block_revision(self, block, context):
                return self.content
            def render(self, block, context):
                return [[('', self.content)]]
        renderer = Dynamic()
        self.registry.register('hub-dependent', renderer)
        messages = (ChatMessage('assistant', '<hub-dependent/>', id='a'),)
        before = self.prepare(messages)
        version = self.registry.version
        renderer.content = 'after'
        after = self.prepare(messages)
        self.assertEqual(version, self.registry.version)
        self.assertNotEqual(tuple(before.lines), tuple(after.lines))
        self.assertIn('after', ''.join(text for row in after.lines for _, text in row))
