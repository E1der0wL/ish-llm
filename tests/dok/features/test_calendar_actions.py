"""Schedule/action separation, inherited overrides and shared delivery admission."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dok import Locale
from dok.features.calendar.actions import ActionBinding, ActionCatalog, ActionLink, ActionSettings
from dok.features.calendar.action_watch import ExternalActions
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.contracts import CalendarSource, ExternalEvent, ExternalRecurrence
from dok.features.calendar.editor import EditorField as F
from dok.features.calendar.edits import CalendarEdits, ActionSavePending
from dok.features.calendar.models import CalendarEvent
from dok.features.calendar.recurrence import Recurrence, RepeatUnit
from dok.features.calendar.reminders import Reminder
from dok.features.calendar.remote_draft import RemoteDraft
from dok.features.calendar.remote_view import RemoteEditor, FormMode
from dok.features.calendar.runtime import CalendarRuntime
from dok.features.calendar.store import CalendarStore
from dok.features.calendar.writes import CalendarMutation, EventValues, WriteKind, CalendarWriteError, WriteProblem


SOURCE = CalendarSource("google", "primary", "Work", enabled=True)
START = datetime(2026, 11, 10, 1, tzinfo=timezone.utc)
MASTER = ExternalEvent("series", "Review", START, START + timedelta(hours=1), version='"v1"',
    recurring=True, repeat=ExternalRecurrence(Recurrence(RepeatUnit.WEEKS)), timezone="Asia/Seoul")
INSTANCE = replace(MASTER, id="instance", series_id=MASTER.id, original_start=START, repeat=ExternalRecurrence())
SETTINGS = ActionSettings(alarms=(Reminder("minutes", 5),), command="printf review")


class ActionStorageTests(unittest.TestCase):
    def test_external_notification_uses_the_configured_display_timezone(self):
        event = ExternalActions._project(SOURCE, INSTANCE, SETTINGS)
        displayed = CalendarRuntime.display_start(event, SimpleNamespace(timezone="Asia/Seoul"))
        self.assertEqual(displayed.isoformat(), "2026-11-10T10:00:00+09:00")
        self.assertEqual(CalendarRuntime.display_start(event, SimpleNamespace(now=lambda: START)), START.astimezone())

    def test_schedule_fields_are_separate_but_public_local_api_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalendarStore(Path(directory))
            event = SETTINGS.apply(CalendarEvent("Review", START.replace(tzinfo=None),
                (START + timedelta(hours=1)).replace(tzinfo=None), timezone="UTC"))
            _, saved = store.save(event)
            data = json.loads(store.path.read_text())
            self.assertNotIn("alarms", data[store.EVENTS_FIELD][0])
            self.assertNotIn("command", data[store.EVENTS_FIELD][0])
            self.assertEqual(data[store.ACTIONS_FIELD][saved.id], SETTINGS.to_dict())
            self.assertEqual(CalendarStore(Path(directory)).load().get(saved.id), saved)

    def test_combined_storage_preserves_settings_and_claims_on_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalendarStore(Path(directory))
            event = SETTINGS.apply(CalendarEvent("Review", START.replace(tzinfo=None),
                (START + timedelta(hours=1)).replace(tzinfo=None), timezone="UTC", revision=1))
            stamp = event.latest_due(START, SETTINGS.alarms[0]).stamp
            store.path.write_text(json.dumps({store.VERSION_FIELD: store.COMBINED_VERSION,
                store.EVENTS_FIELD: [event.to_dict()], store.RECEIPTS_FIELD: {event.reminder_key(SETTINGS.alarms[0]): stamp},
                store.COMMANDS_FIELD: {event.command_key: event.start.isoformat()}}))
            self.assertFalse(store.check(START, commands=True).due)
            store.save_actions(ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS))
            self.assertEqual(json.loads(store.path.read_text())[store.VERSION_FIELD], store.VERSION)
            self.assertEqual(store.load().events[0].command, SETTINGS.command)

    def test_empty_instance_override_suppresses_parent_and_survives_restart(self):
        parent = ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS)
        child = ActionBinding(ActionLink.for_event(SOURCE, INSTANCE), ActionSettings())
        with tempfile.TemporaryDirectory() as directory:
            store = CalendarStore(Path(directory))
            store.save_actions(parent)
            store.save_actions(child)
            catalog = CalendarStore(Path(directory)).load_actions()
            self.assertFalse(catalog.effective(child.link).active)
            self.assertEqual(catalog.effective(ActionLink(*SOURCE.identity, "next", MASTER.id)), SETTINGS)
            store.delete_actions(parent.link)
            self.assertFalse(store.load_actions().bindings)

    def test_external_receipts_survive_move_title_change_and_restart(self):
        catalog = ActionCatalog((ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS, 1),))
        with tempfile.TemporaryDirectory() as directory:
            store = CalendarStore(Path(directory))
            store.save_actions(replace(catalog.bindings[0], revision=0))
            event = ExternalActions._project(SOURCE, INSTANCE, SETTINGS)
            store.check(START - timedelta(minutes=6), commands=True, external=(event,), expected_actions=catalog)
            check = store.check(START, commands=True, external=(event,), expected_actions=catalog)
            self.assertEqual((len(check.due), len(check.commands)), (1, 1))
            moved = replace(event, title="Changed", start=event.start + timedelta(hours=2), end=event.end + timedelta(hours=2))
            second = CalendarStore(Path(directory))
            second.check(START + timedelta(hours=1), commands=True, external=(moved,), expected_actions=catalog)
            check = second.check(START + timedelta(hours=2), commands=True, external=(moved,), expected_actions=catalog)
            self.assertFalse(check.commands or check.due)

    def test_changed_binding_rejects_old_projection_before_command_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CalendarStore(Path(directory))
            event = ExternalActions._project(SOURCE, INSTANCE, SETTINGS)
            catalog, binding = store.save_actions(ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS))
            store.check(START - timedelta(seconds=1), commands=True, external=(event,), expected_actions=catalog)
            store.save_actions(replace(binding, settings=ActionSettings()))
            check = store.check(START, commands=True, external=(event,), expected_actions=catalog)
            self.assertFalse(check.commands or check.due)


class ExternalActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_parent_read_does_not_replace_expanded_month_occurrences(self):
        writer = SimpleNamespace(can_write=lambda source: True, get=AsyncMock(side_effect=[INSTANCE, MASTER]))
        registry = SimpleNamespace(revision=1, resolve=lambda *args: writer, require=lambda *args: writer)
        state = SimpleNamespace(source=SOURCE, writable=True, events=(INSTANCE,))
        publish = Mock()
        edits = CalendarEdits(registry, lambda: (state,), publish)
        self.assertEqual(await edits.edit_target(SOURCE, INSTANCE.id), MASTER)
        publish.assert_called_once_with(SOURCE, INSTANCE.id, INSTANCE, registry.revision)

    async def test_window_uses_alarm_offset_not_visible_month_and_overrides_disable(self):
        alarm = ActionSettings(alarms=(Reminder("weeks", 8),), command="printf review")
        catalog = ActionCatalog((ActionBinding(ActionLink.for_event(SOURCE, MASTER), alarm),
                                 ActionBinding(ActionLink.for_event(SOURCE, INSTANCE), ActionSettings())))
        provider = SimpleNamespace(get=AsyncMock(return_value=MASTER), instances=AsyncMock(return_value=(INSTANCE,)))
        registry = SimpleNamespace(require=Mock(return_value=provider))
        edits = CalendarEdits(registry, lambda: (), Mock())
        watcher = ExternalActions(SimpleNamespace(), registry, edits, Mock())
        self.assertFalse(await watcher._fetch(SOURCE, catalog, START))
        bounds = [call.args[2:] for call in provider.instances.await_args_list]
        self.assertTrue(any(lower == START + timedelta(weeks=8) for lower, upper in bounds))
        self.assertTrue(any(lower == START for lower, upper in bounds))

    async def test_background_failure_withdraws_candidates_and_disabled_source_stops_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = CalendarRuntime(Path(directory), Mock(), Mock())
            await runtime.save_actions(ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS))
            provider = SimpleNamespace(get=AsyncMock(side_effect=OSError("offline")))
            registry = SimpleNamespace(require=Mock(return_value=provider), revision=1)
            watcher = ExternalActions(runtime, registry, CalendarEdits(registry, lambda: (), Mock()), Mock())
            watcher.sync((SOURCE,))
            for _ in range(100):
                if watcher.error:
                    break
                await asyncio.sleep(0)
            self.assertEqual(watcher.error, "OSError")
            self.assertFalse(runtime._external)
            count = provider.get.await_count
            watcher.sync((replace(SOURCE, enabled=False),))
            await asyncio.sleep(0)
            self.assertEqual(provider.get.await_count, count)
            watcher.close()
            runtime.close()

    async def test_partial_save_retries_only_local_persistence(self):
        actions = SimpleNamespace(save_actions=AsyncMock(side_effect=[OSError("disk"), None]), delete_actions=AsyncMock())
        edits = CalendarEdits(SimpleNamespace(), lambda: (), Mock(), actions=actions)
        edits.apply = AsyncMock(return_value=MASTER)
        mutation = CalendarMutation(SOURCE, WriteKind.UPDATE, MASTER.id, EventValues.from_event(MASTER), MASTER)
        binding = ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS)
        with self.assertRaises(ActionSavePending) as error:
            await edits.save(mutation, binding)
        await edits.finish_actions(error.exception)
        self.assertEqual(edits.apply.await_count, 1)
        self.assertEqual(actions.save_actions.await_count, 2)

    async def test_recurrence_opens_parent_directly_and_deletes_the_series(self):
        catalog = ActionCatalog((ActionBinding(ActionLink.for_event(SOURCE, MASTER), SETTINGS, 1),))
        edits = SimpleNamespace(destinations=(SOURCE,), action_catalog=AsyncMock(return_value=catalog),
            get=AsyncMock(side_effect=lambda source, identifier: MASTER if identifier == MASTER.id else INSTANCE))
        async def target(source, event_id):
            return await CalendarEdits.edit_target(edits, source, event_id)
        edits.edit_target = target
        form = RemoteEditor(edits, START.date(), lambda: SimpleNamespace(timezone="Asia/Seoul", now=lambda: START),
            CalendarConfig, Locale, Mock(), Mock(), Mock(), Mock())
        form.load(SOURCE, INSTANCE.id)
        await form._task
        self.assertEqual(form.mode, FormMode.EDIT)
        self.assertEqual(form.draft.event_id, MASTER.id)
        self.assertTrue(all(form.draft.supports(field) for field in (F.REPEAT, F.ALARMS, F.COMMAND)))
        self.assertIsNone(form.draft.action_change())
        self.assertEqual([call.args[1] for call in edits.get.await_args_list], [INSTANCE.id, MASTER.id])
        form.load(SOURCE, INSTANCE.id, delete=True)
        await form._task
        self.assertEqual(form.mode, FormMode.DELETE)
        mutation = form.draft.mutation(delete=True)
        self.assertEqual((mutation.event_id, mutation.original.version), (MASTER.id, MASTER.version))
        form.dispose()

    async def test_missing_or_locked_parent_never_falls_back_to_instance_editing(self):
        for parent, expected in ((None, WriteProblem.UNAVAILABLE), (replace(MASTER, locked=True), WriteProblem.DENIED)):
            edits = SimpleNamespace(get=AsyncMock(side_effect=[INSTANCE, parent]))
            with self.subTest(parent=parent), self.assertRaises(CalendarWriteError) as error:
                await CalendarEdits.edit_target(edits, SOURCE, INSTANCE.id)
            self.assertEqual(error.exception.problem, expected)

    async def test_nonrepeating_edit_uses_original_event_without_parent_read(self):
        event = replace(MASTER, recurring=False, repeat=ExternalRecurrence())
        edits = SimpleNamespace(get=AsyncMock(return_value=event))
        self.assertEqual(await CalendarEdits.edit_target(edits, SOURCE, event.id), event)
        edits.get.assert_awaited_once_with(SOURCE, event.id)
