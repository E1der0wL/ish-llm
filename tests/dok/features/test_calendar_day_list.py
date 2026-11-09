"""Day-list navigation, local/external routing and shared editor presentation."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock

from dok import DockConfig, Locale
from dok.features.calendar.contracts import CalendarSource, ExternalEvent
from dok.features.calendar.actions import ActionCatalog
from dok.features.calendar.editor import EditorField as F
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.remote_view import FormMode
from dok.features.calendar.service import CalendarListing
from dok.features.calendar.store import CalendarSnapshot
from dok.features.calendar.view import CalendarPage, Confirmation
from dok.locales import Message as M


class CalendarDayListTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = DockConfig.initial()
        self.locale = Locale.resolve("en")
        self.event = CalendarEvent("Local meeting", datetime(2026, 11, 10, 10), datetime(2026, 11, 10, 11))
        self.source = CalendarSource(connection="google", calendar="primary", label="Work", enabled=True)
        self.external = ExternalEvent("remote", "Remote meeting", datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
                                      datetime(2026, 11, 10, 13, tzinfo=timezone.utc), version='"v1"')
        self.local = SimpleNamespace(snapshot=CalendarSnapshot((self.event,)), error="", save=Mock(), delete=Mock())
        self.reads, self.writes = [], []
        async def get(source, event_id):
            self.reads.append((source, event_id))
            return self.external
        async def apply(mutation):
            self.writes.append(mutation)
            return None
        async def save(mutation, binding=None):
            return await apply(mutation)
        edits = SimpleNamespace(destinations=(self.source,), editable=lambda identifier: True,
                                source_for=lambda identifier: self.source, get=get, edit_target=get, apply=apply, save=save,
                                action_catalog=AsyncMock(return_value=ActionCatalog()))
        self.service = SimpleNamespace(edits=edits, sources=(), refresh=Mock(),
            snapshot=CalendarListing(self.local.snapshot, (self.external.project(self.source, "UTC"),)))
        self.page = CalendarPage(lambda: SimpleNamespace(now=lambda: datetime(2026, 11, 10, 9), timezone="UTC"),
            lambda: self.config.popups.calendar, self.local, calendar=self.service, locale=lambda: self.locale)

    def lines(self, height=42):
        return ["".join(fragment[1] for fragment in row) for row in self.page.draw_body(140, height, self.config.theme)]

    def test_populated_day_lists_events_and_empty_day_shows_editor(self):
        lines = self.lines()
        self.assertTrue(self.page.show_list)
        self.assertIn("Events", "\n".join(lines))
        self.assertIn("Remote meeting", "\n".join(lines))
        self.assertLess(lines[-3].index("Add"), lines[-3].index("Edit"))
        self.assertLess(lines[-3].index("Edit"), lines[-3].index("Delete"))
        self.assertEqual(lines[1].split("│")[-1].strip(), "")  # Date/list heading padding.
        self.assertNotIn("Edit", "\n".join(lines[:4]))
        self.assertNotIn("F5", "\n".join(lines))
        self.page._select(date(2026, 11, 11))
        self.assertFalse(self.page.show_list)
        self.assertIn(self.locale.text(M.TITLE), "\n".join(self.lines()))
        self.page.key("enter", "")
        self.assertIsNone(self.page.draft.original)

    def test_editor_heading_stays_fixed_during_scroll_and_confirmation_clears_it(self):
        self.page.key("enter", "")
        self.page.key("enter", "")
        self.page._form.scroll(12)
        lines = self.lines(height=20)
        self.assertIn("2026-11-10", lines[0].split("│")[-1])
        self.page.confirm = Confirmation.DELETE
        lines = self.lines(height=20)
        self.assertEqual(lines[0].split("│")[-1].strip(), "")
        self.assertIn(self.locale.text(M.DELETE_SERIES), lines[1])

    async def test_remote_confirmation_uses_the_same_blank_heading_row(self):
        self.page.key("enter", "")
        self.page.key("down", "")
        self.page.key("enter", "")
        await self.page._remote._task
        self.assertIn("2026-11-10", self.lines()[0].split("│")[-1])
        self.page._remote._activate(F.DELETE)
        lines = self.lines()
        self.assertEqual(lines[0].split("│")[-1].strip(), "")
        self.assertIn("Delete", lines[1])

    async def test_selected_event_is_stable_when_refresh_reorders_the_day(self):
        self.page.key("enter", "")
        self.page.key("down", "")
        earlier = replace(self.event, id="earlier", start=datetime(2026, 11, 10, 8), end=datetime(2026, 11, 10, 9))
        self.service.snapshot = CalendarListing(CalendarSnapshot((self.event, earlier)), self.service.snapshot.external)
        self.lines()
        self.assertEqual(self.page.event_index, 2)
        self.page.key("enter", "")
        self.assertIsNotNone(self.page._remote)
        await self.page._remote._task

    async def test_google_delete_from_list_loads_version_then_requires_confirmation(self):
        self.page.key("enter", "")
        self.page.key("down", "")
        self.page.key("delete", "")
        form = self.page._remote
        await form._task
        self.assertEqual(form.mode, FormMode.DELETE)
        self.assertEqual(self.reads, [(self.source, self.external.id)])
        self.assertFalse(self.writes)
        form.key("left", "")
        form.key("enter", "")
        await form._task
        self.assertEqual(self.writes[0].original.version, self.external.version)
        self.local.delete.assert_not_called()

    async def test_local_and_google_forms_share_fields_picker_and_footer(self):
        self.page.key("enter", "")
        self.page.key("enter", "")
        local_lines = self.lines()
        local_time = local_lines[self.page._field_rows[F.START_DATE]]
        self.page.key("escape", "")
        self.assertTrue(self.page.show_list)
        self.page.key("down", "")
        self.page.key("enter", "")
        await self.page._remote._task
        remote_lines = self.lines()
        form = self.page._remote._form
        self.assertEqual(local_time, remote_lines[self.page._remote_region.top + form.field_rows[F.START_DATE]])
        self.assertIn("Save", local_lines[-3])
        self.assertIn("Save", remote_lines[-3])
        self.page._remote._activate(F.START_DATE)
        self.assertIsNotNone(self.page._remote._picker)

    def test_external_removal_while_list_focused_can_start_a_new_event(self):
        self.page.key("enter", "")
        self.service.snapshot = CalendarListing(CalendarSnapshot())
        self.assertIsInstance(self.page.status_text(), str)
        self.page.key("enter", "")
        self.assertIsNotNone(self.page.draft)
        self.assertIsNone(self.page.draft.original)

    def test_footer_tab_order_add_edit_delete_and_delete_confirmation(self):
        self.page.key("enter", "")
        self.page.key("tab", "")
        self.page.key("enter", "")
        self.assertIsNone(self.page.draft.original)
        self.page.key("escape", "")
        self.assertTrue(self.page.show_list)
        for _ in range(2):
            self.page.key("tab", "")
        self.page.key("enter", "")
        self.assertEqual(self.page.draft.original.id, self.event.id)
        self.page.key("escape", "")
        for _ in range(3):
            self.page.key("tab", "")
        self.page.key("enter", "")
        self.assertEqual(self.page.confirm, Confirmation.DELETE)
        self.local.delete.assert_not_called()
        self.page.key("escape", "")
        self.assertIsNone(self.page.confirm)
        self.local.delete.assert_not_called()

    async def asyncTearDown(self):
        remote = self.page._remote
        self.page.dispose()
        if remote is not None and remote._task is not None:
            await asyncio.gather(remote._task, return_exceptions=True)
