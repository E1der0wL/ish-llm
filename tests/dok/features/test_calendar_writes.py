"""External draft rebasing, source choice and write coordination invariants."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dok import Locale, Theme
from dok.connections import ConnectionRegistry, ConnectionSpec
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.contracts import CalendarSource, ExternalEvent
from dok.features.calendar.edits import CalendarEdits
from dok.features.calendar.remote_draft import RemoteDraft
from dok.features.calendar.editor import EditorField as F
from dok.features.calendar.remote_view import RemoteEditor, FormMode
from dok.features.calendar.view import CalendarColors
from dok.features.calendar.writes import CalendarWriter, CalendarWriteError, CalendarConflict, WriteProblem

SOURCE = CalendarSource(connection="google", calendar="primary", label="Work", enabled=True)
EVENT = ExternalEvent("one", "Review", datetime(2026, 11, 10, 1, tzinfo=timezone.utc),
                      datetime(2026, 11, 10, 2, tzinfo=timezone.utc), "Room 1", version='"v1"')


class Writer:
    def __init__(self):
        self.calls = []
        self.event = EVENT
        self.problem = None
        self.gate = None

    def can_write(self, source):
        return True

    def resolve(self, capability):
        return self if capability is CalendarWriter else None

    async def get(self, source, identifier):
        return self.event

    async def apply(self, mutation):
        self.calls.append(mutation)
        if self.gate:
            await self.gate.wait()
        if self.problem:
            raise CalendarWriteError(self.problem)
        return self.event


class CalendarDraftTests(unittest.TestCase):
    def test_destination_change_is_unsaved_and_reverting_clears_it(self):
        draft = RemoteDraft(SOURCE, date(2026, 11, 10), "Asia/Seoul", now=datetime(2026, 11, 10, 13))
        self.assertFalse(draft.dirty)
        draft.change_source(replace(SOURCE, calendar="personal"))
        self.assertTrue(draft.dirty)
        draft.change_source(SOURCE)
        self.assertFalse(draft.dirty)
        draft.buffer(F.COMMAND).text = "printf review"
        self.assertTrue(draft.dirty)
        draft.buffer(F.COMMAND).text = ""
        self.assertFalse(draft.dirty)

    def test_rebase_keeps_only_our_changes_and_requires_another_save(self):
        draft = RemoteDraft(SOURCE, date(2026, 11, 10), "Asia/Seoul", original=EVENT)
        draft.buffer(F.TITLE).text = "My title"
        latest = replace(EVENT, location="Room 2", version='"v2"')
        rebased = draft.rebased(latest, date(2026, 11, 10), keep=True)
        self.assertEqual((rebased.values().title, rebased.values().location), ("My title", "Room 2"))
        self.assertTrue(rebased.dirty)
        self.assertEqual(rebased.mutation().original.version, '"v2"')
        reloaded = draft.rebased(latest, date(2026, 11, 10))
        self.assertFalse(reloaded.dirty)
        self.assertEqual(reloaded.values().title, EVENT.title)

    def test_new_draft_id_is_stable_and_dates_keep_one_time_defaults(self):
        draft = RemoteDraft(SOURCE, date(2026, 11, 10), "Asia/Seoul", now=datetime(2026, 11, 10, 13, 42))
        self.assertEqual(draft.temporal(F.START_TIME).isoformat(), "14:00:00")
        self.assertEqual(draft.temporal(F.END_TIME).isoformat(), "15:00:00")
        first = draft.mutation().event_id
        draft.choose(F.START_DATE, date(2026, 11, 9))
        self.assertEqual(draft.temporal(F.END_DATE), date(2026, 11, 10))
        self.assertEqual(draft.mutation().event_id, first)
        draft.all_day = True
        self.assertEqual(draft.values().end, date(2026, 11, 11))


class CalendarEditServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writer, self.registry = Writer(), ConnectionRegistry()
        self.registry.configure((ConnectionSpec(SOURCE.connection, "fake", True, 1, lambda: self.writer),))
        self.state = SimpleNamespace(source=SOURCE, writable=True, events=(EVENT,))
        self.published = Mock()
        self.edits = CalendarEdits(self.registry, lambda: (self.state,), self.published)

    async def test_uncertain_result_blocks_duplicate_and_explicit_read_adopts_latest(self):
        draft = RemoteDraft(SOURCE, date(2026, 11, 10), "Asia/Seoul", original=EVENT)
        draft.buffer(F.TITLE).text = "Changed"
        mutation = draft.mutation()
        self.writer.problem = WriteProblem.UNCERTAIN
        for _ in range(2):
            with self.assertRaises(CalendarWriteError):
                await self.edits.apply(mutation)
        self.assertEqual(len(self.writer.calls), 1)
        self.published.assert_not_called()
        self.assertEqual(await self.edits.get(SOURCE, EVENT.id), EVENT)
        self.published.assert_called_once()

    async def test_missing_event_does_not_release_uncertain_request_for_replay(self):
        draft = RemoteDraft(SOURCE, date(2026, 11, 10), "Asia/Seoul", original=EVENT)
        self.writer.problem = WriteProblem.UNCERTAIN
        with self.assertRaises(CalendarWriteError):
            await self.edits.apply(draft.mutation(delete=True))
        self.writer.event = None
        self.assertIsNone(await self.edits.get(SOURCE, EVENT.id))
        with self.assertRaises(CalendarWriteError):
            await self.edits.apply(draft.mutation(delete=True))
        self.assertEqual(len(self.writer.calls), 1)

    async def test_source_permission_and_disabled_connection_remove_destinations(self):
        self.assertEqual(self.edits.destinations, (SOURCE,))
        self.state.writable = False
        self.assertEqual(self.edits.destinations, ())
        self.state.writable = True
        self.registry.configure((ConnectionSpec(SOURCE.connection, "fake", False, 1, lambda: self.writer),))
        self.assertEqual(self.edits.destinations, ())

    async def test_remote_form_requires_delete_confirmation_and_blocks_close_while_saving(self):
        back, local = Mock(), Mock()
        form = RemoteEditor(self.edits, date(2026, 11, 10),
            lambda: SimpleNamespace(now=lambda: datetime(2026, 11, 10), timezone="Asia/Seoul"), CalendarConfig,
            Locale, lambda: None, back, local,
            lambda day=None, weekday=None: CalendarColors.role(day.weekday() if day else weekday))
        form.key("enter", "")
        local.assert_called_once()
        form.load(SOURCE, EVENT.id)
        await form._task
        form._activate(F.DELETE)
        self.assertEqual(form.mode, FormMode.DELETE)
        self.assertEqual(self.writer.calls, [])
        rows = form.draw(28, 9, Theme())
        text = [''.join(item[1] for item in row).strip() for row in rows]
        self.assertEqual(text[-3], '')
        self.assertIn('Delete', text[-2])
        self.assertIn('Cancel', text[-2])
        self.assertEqual(text[-1], '')
        form.key('end', '')
        self.assertEqual(form.mode, FormMode.DELETE)
        self.assertEqual(self.writer.calls, [])
        form.key("escape", "")
        form.draft.buffer(F.TITLE).text = "Changed"
        self.writer.gate = asyncio.Event()
        form.key("f2", "")
        await asyncio.sleep(0)
        self.assertFalse(form.request_close(back))
        back.assert_not_called()
        self.writer.gate.set()
        await form._task
        self.assertEqual(len(self.writer.calls), 1)
        self.assertTrue(form.request_close(back))
        form.dispose()

    async def test_uncertain_recheck_preserves_draft_and_requires_explicit_rebase(self):
        form = RemoteEditor(self.edits, date(2026, 11, 10),
            lambda: SimpleNamespace(now=lambda: datetime(2026, 11, 10), timezone="Asia/Seoul"), CalendarConfig,
            Locale, lambda: None, Mock(), Mock(),
            lambda day=None, weekday=None: CalendarColors.role(day.weekday() if day else weekday))
        form.load(SOURCE, EVENT.id)
        await form._task
        form.draft.buffer(F.TITLE).text = "Preserve this draft"
        self.writer.problem = WriteProblem.UNCERTAIN
        form.key("f2", "")
        await form._task
        self.assertEqual(form.mode, FormMode.UNCERTAIN)
        form.key("enter", "")
        await form._task
        self.assertEqual(form.mode, FormMode.REVIEW)
        self.assertEqual(form.draft.buffer(F.TITLE).text, "Preserve this draft")
        self.assertEqual(len(self.writer.calls), 1)
        form.key("tab", "")
        form.key("enter", "")
        self.assertEqual(form.mode, FormMode.EDIT)
        self.assertTrue(form.draft.dirty)
        self.assertEqual(len(self.writer.calls), 1)
        form.dispose()
