"""Real ish layout: popup focus, dispatch, mouse, resize and prompt lifetime."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.selection import SelectionState
from prompt_toolkit.widgets import Label, TextArea

from dok import DockConfig, PopupContent, install, uninstall
from dok.core.state import DockMode
from dok.features.weather.models import ForecastDay, WeatherKind, WeatherSnapshot
from dok.features.calendar.models import CalendarEvent, RepeatUnit
from dok.features.calendar.editor import EditorField
from dok.features.calendar.reminders import Reminder
from dok.features.calendar.commands import CommandResult
from tests.dok.support import HostPrompt, Output, render, screen_lines, wait_running


async def until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(.005)


class PopupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        context = create_pipe_input()
        self.pipe = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.output = Output(columns=100, rows=30)
        self.prompt = HostPrompt(self.pipe, self.output)
        self.dock = install(self.prompt, config_path=Path(self.directory.name) / "config.jsonc")
        self.dock.configure(DockConfig(audio={"enabled": False}, media={"enabled": False}, toolbar={"enabled": False}))
        self.task = None

    async def asyncTearDown(self):
        if self.task is not None and not self.task.done():
            self.prompt.app.exit(result="")
            await self.task
        uninstall(self.prompt)

    async def start(self, text="draft"):
        self.task = asyncio.create_task(self.prompt.read_and_dispatch(default=text))
        await wait_running(self.prompt.app)
        return await render(self.prompt.app)

    async def send(self, data):
        self.pipe.send_bytes(data)
        await asyncio.sleep(.05)
        return await render(self.prompt.app)

    async def test_scheduled_submission_preserves_busy_prompt_and_uses_native_dispatch_when_idle(self):
        await self.start("unfinished")
        before = self.prompt.default_buffer.document
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.SKIPPED)
        self.assertEqual(self.prompt.default_buffer.document, before)
        self.prompt.default_buffer.text = ""
        self.dock.open_calendar()
        await render(self.prompt.app)
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.SKIPPED)
        self.dock.close_popup()
        await render(self.prompt.app)
        self.prompt.interactive_shell.continuation_active = asyncio.Event()
        self.prompt.interactive_shell.continuation_active.set()
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.SKIPPED)
        self.prompt.interactive_shell.continuation_active.clear()
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.SUBMITTED)
        self.assertEqual(await asyncio.wait_for(self.task, 2), "printf scheduled")
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.SKIPPED)

    async def test_scheduled_command_rejected_by_host_restores_empty_document(self):
        from ish.shell.input import InputRejected
        await self.start("")
        def reject(text):
            raise InputRejected("transport unavailable")
        self.prompt.transport_check = reject
        self.assertEqual(self.dock._submission.execute_command("printf scheduled"), CommandResult.FAILED)
        self.assertEqual(self.prompt.default_buffer.text, "")
        self.assertFalse(self.task.done())

    async def test_host_language_and_explicit_override_update_open_popup_without_losing_draft(self):
        with patch("dok.integrations.ish.language.HostLanguage.current", return_value="ko_KR"):
            await self.start()
            self.dock.open_calendar()
            await render(self.prompt.app)
            page = self.dock._popups.page
            page.key("enter", "")
            page.key("<any>", "My 일정")
            self.assertTrue(page.status_text().startswith("제목"))
            self.dock.configure(replace(self.dock.config, language="en"))
            await render(self.prompt.app)
            self.assertIs(self.dock._popups.page, page)
            self.assertTrue(page.status_text().startswith("Title"))
            self.assertEqual(page.draft.buffer(EditorField.TITLE).text, "My 일정")
            self.assertTrue(self.prompt.app.mouse_support())
            self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_modal_preserves_document_selection_and_blocks_shell_keys(self):
        calls = []
        self.prompt.set_key("f2", handler=lambda event: calls.append("host"))
        await self.start()
        buffer = self.prompt.default_buffer
        buffer.document = Document("first\nsecond", 8, SelectionState(2))
        original = buffer.document
        control = self.prompt.app.layout.current_control
        rebuilds = self.prompt.layout_rebuilds
        self.dock.open_popup(PopupContent.plain("Title", "Body"))
        lines = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("Title" in line for line in lines), lines)
        self.assertTrue(self.dock._popups.modal)
        self.assertFalse(self.dock.config.dock.mouse.enabled)
        self.assertTrue(self.prompt.app.mouse_support())
        await self.send(b"text\x03\x04\x1a\x17\x1bOQ")
        await self.send(b"\x1bc\x1bw\x1bn")
        self.assertEqual(buffer.document, original)
        self.assertEqual(calls, [])
        self.assertFalse(self.task.done())
        await self.send(b"\x1b")
        self.assertTrue(self.dock._popups.modal)  # Root Escape stays in a custom popup.
        self.dock.close_popup()
        await until(lambda: not self.dock._popups.modal)
        self.assertIs(self.prompt.app.layout.current_control, control)
        self.assertFalse(self.prompt.app.mouse_support())
        self.assertEqual(buffer.document, original)
        self.assertEqual(self.prompt.layout_rebuilds, rebuilds)
        await self.send(b"\x1bOQ")
        self.assertEqual(calls, ["host"])

    async def test_calendar_picker_keys_cancel_locally_and_save_through_existing_runtime(self):
        await self.start("prompt text")
        original_prompt = self.prompt.default_buffer.document
        self.dock.open_calendar()
        await render(self.prompt.app)
        page = self.dock._popups.page
        page.key("enter", "")
        page.key("<any>", "Picker meeting")
        original_date = page.draft.temporal_value(EditorField.START_DATE)
        initial_end = page.draft.temporal_value(EditorField.END_TIME)
        page.field = EditorField.START_DATE
        await self.send(b"\r")
        self.assertIsNotNone(page._picker)
        await self.send(b"\x1b[C")
        await self.send(b"\x1b")
        await until(lambda: page._picker is None)
        self.assertTrue(self.dock._popups.modal)
        self.assertTrue(page.editing)
        self.assertEqual(page.draft.temporal_value(EditorField.START_DATE), original_date)
        page.field = EditorField.END_TIME
        await self.send(b"\r\t\x1b[B\t\x1b[B\r")
        await until(lambda: page._picker is None)
        expected_end = initial_end.replace(minute=min(59, initial_end.minute + 1), second=min(59, initial_end.second + 1))
        self.assertEqual(page.draft.temporal_value(EditorField.END_TIME), expected_end)
        await self.send(b"\x1bOQ")
        await until(lambda: page.show_list and not page.busy)
        saved = page.snapshot.on_day(page.selected)[0]
        self.assertEqual(saved.title, "Picker meeting")
        self.assertEqual(saved.end.time(), expected_end)
        self.assertEqual(self.prompt.default_buffer.document, original_prompt)
        self.dock.close_popup()
        await render(self.prompt.app)
        self.assertFalse(self.dock._popups.modal)
        self.assertFalse(self.prompt.app.mouse_support())

    async def test_calendar_weather_keys_navigation_and_escape(self):
        await self.start()
        await self.send(b"\x1bc")
        self.assertTrue(self.dock._popups.modal)
        self.assertTrue(self.prompt.app.mouse_support())
        before = self.dock._popups.page.selected.replace(day=1)
        self.assertEqual(self.dock._popups.page.content().title, "Calendar")
        await self.send(b"\x1b[6~")
        self.assertNotEqual(self.dock._popups.page.selected.replace(day=1), before)
        self.assertEqual(self.dock._popups.page.content().title, "Calendar")
        await self.send(b"\x1b")
        self.assertTrue(self.dock._popups.modal)
        await self.send(b"\x1bc")
        await until(lambda: not self.dock._popups.modal)
        await self.send(b"\x1bw")
        self.assertEqual(self.dock._popups.page.content().title, "Weather")
        self.assertTrue(self.prompt.app.mouse_support())
        lines = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("--°" in line for line in lines), lines)
        await self.send(b"\x1b")
        self.assertTrue(self.dock._popups.modal)
        await self.send(b"\x1bw")
        await until(lambda: not self.dock._popups.modal)
        self.assertFalse(self.dock._popups.modal)
        self.assertFalse(self.prompt.app.mouse_support())
        self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_rapid_escape_then_opener_and_repeated_back_do_not_swallow_keys(self):
        await self.start('preserved')
        for opener in (b'\x1bc', b'\x1bw'):
            await self.send(opener)
            self.assertTrue(self.dock._popups.modal)
            self.pipe.send_bytes(b'\x1b' + opener)
            await until(lambda: not self.dock._popups.modal)
        await self.send(b'\x1bc\r')
        page = self.dock._popups.page
        self.assertTrue(page.editing)
        self.pipe.send_bytes(b'\x1b\x1b')
        await until(lambda: not page.editing)
        # An ignored Alt chord remains local after back navigation.
        await self.send(b'\x1bx')
        self.assertTrue(self.dock._popups.modal)
        self.assertEqual(self.prompt.default_buffer.text, 'preserved')
        await self.send(b'\x1bc')
        await until(lambda: not self.dock._popups.modal)

    async def test_modal_mouse_blocks_click_through_and_restores_host_preference(self):
        config = self.dock.config
        uninstall(self.prompt)
        previous = to_filter(True)
        self.prompt.app.mouse_support = previous
        self.dock = install(self.prompt, config_path=Path(self.directory.name) / "config.jsonc")
        self.dock.configure(config)
        await self.start()
        self.dock.open_popup(PopupContent.plain("Modal", "Mouse scope"))
        await render(self.prompt.app)
        self.assertTrue(self.prompt.app.mouse_support())
        regions = self.dock._popups.layer.mouse_regions
        x, y = next((x, y) for y in range(self.output.rows) for x in range(self.output.columns)
                    if not any(left <= x < right and y == row for left, right, row in regions))
        calls = []
        self.dock._mouse._source.mouse_handlers[y][x] = lambda event: calls.append(event)
        with set_app(self.prompt.app):
            self.prompt.app.renderer.mouse_handlers.mouse_handlers[y][x](MouseEvent(
                Point(x, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        self.assertEqual(calls, [])
        self.assertEqual(self.prompt.default_buffer.text, "draft")
        self.dock.close_popup()
        await render(self.prompt.app)
        self.assertTrue(self.prompt.app.mouse_support())
        self.assertFalse(self.dock.config.dock.mouse.enabled)
        uninstall(self.prompt)
        self.assertIs(self.prompt.app.mouse_support, previous)

    async def test_escape_latency_split_terminal_sequences_and_timing_restoration(self):
        app = self.prompt.app
        original = (app.ttimeoutlen, app.timeoutlen)
        await self.start("preserved")
        self.dock.open_calendar()
        await render(app)
        page = self.dock._popups.page
        configured = self.dock.config.popups.input.escape_s
        self.assertEqual((app.ttimeoutlen, app.timeoutlen), (configured, configured))
        before = page.selected
        # Escape must remain a prefix for both a fragmented CSI and an Alt chord.
        async def split_input(prefix, suffix):
            original_read = app.input.read_keys
            consumed = asyncio.Event()
            sent_suffix = False

            def read_keys():
                nonlocal sent_suffix
                result = original_read()
                if not sent_suffix:
                    # Send the suffix only after a real read consumed the
                    # prefix. A sleep-based split can exceed escape_s under
                    # load or coalesce both writes into one terminal read.
                    sent_suffix = True
                    self.pipe.send_bytes(suffix)
                else:
                    consumed.set()
                return result

            with patch.object(app.input, "read_keys", read_keys):
                self.pipe.send_bytes(prefix)
                await asyncio.wait_for(consumed.wait(), 3)

        await split_input(b"\x1b[", b"C")
        # Rendering and input processing are separate event-loop callbacks.
        # Wait for an observable result, while still failing if ESC closed it.
        await until(lambda: page.selected != before or not self.dock._popups.modal)
        self.assertEqual(page.selected, before + timedelta(days=1))
        await split_input(b"\x1b", b"x")
        self.assertTrue(self.dock._popups.modal)
        self.assertEqual(self.prompt.default_buffer.text, "preserved")
        await self.send(b"\r")
        self.assertTrue(page.editing)
        started = asyncio.get_running_loop().time()
        self.pipe.send_bytes(b"\x1b")
        async with asyncio.timeout(.5):
            await until(lambda: not page.editing)
        elapsed = asyncio.get_running_loop().time() - started
        print(f"ESC back pipe latency: {elapsed * 1000:.1f} ms", flush=True)
        self.assertTrue(self.dock._popups.modal)
        await self.send(b"\x1bc")
        await until(lambda: not self.dock._popups.modal)
        self.assertEqual((app.ttimeoutlen, app.timeoutlen), original)
        self.assertEqual(self.prompt.default_buffer.text, "preserved")

    async def test_popup_timing_restores_on_focus_change_reload_and_uninstall(self):
        app = self.prompt.app
        app.ttimeoutlen, app.timeoutlen = .7, None
        other = TextArea(text="external")
        self.prompt.set_float(other, top=0, left=0, width=30, height=5)
        await self.start()
        self.dock.open_calendar()
        await render(app)
        app.layout.focus(other)
        await render(app)
        self.assertEqual((app.ttimeoutlen, app.timeoutlen), (.7, None))
        app.layout.focus(self.prompt.default_buffer)
        await render(app)
        self.assertTrue(self.dock._popups.modal)
        self.dock.configure(replace(self.dock.config, popups=replace(self.dock.config.popups, input={"escape_s": .08})))
        await render(app)
        self.assertEqual((app.ttimeoutlen, app.timeoutlen), (.08, .08))
        # Preserve a later external writer's value during cleanup.
        app.timeoutlen = .9
        uninstall(self.prompt)
        self.assertEqual((app.ttimeoutlen, app.timeoutlen), (.7, .9))

    async def test_weather_popup_shares_one_fetch_without_opening_dock(self):
        self.dock.configure(replace(self.dock.config, widgets={"weather": {"location": {"lat": 37, "lon": 127}}}))
        backend = AsyncMock()
        backend.fetch.return_value = WeatherSnapshot((ForecastDay(date(2026, 11, 7), WeatherKind.CLEAR, 18, 4),))
        self.dock._dashboard._weather._backend = backend
        await self.start()
        self.dock.open_weather()
        await until(lambda: bool(self.dock.weather.days))
        await render(self.prompt.app)
        self.assertFalse(self.dock.requested_visible)
        self.assertEqual(backend.fetch.await_count, 1)
        self.dock.close_popup()
        await render(self.prompt.app)
        self.dock.open_weather()
        await render(self.prompt.app)
        self.assertEqual(backend.fetch.await_count, 1)

    async def test_failed_weather_retries_on_dock_reopen_in_basic_mode_only_once(self):
        self.dock.configure(replace(self.dock.config, widgets={"weather": {"location": {"lat": 0, "lon": 0}}}))
        snapshot = WeatherSnapshot((ForecastDay(date(2026,11,7), WeatherKind.CLEAR,18,4),))
        backend = AsyncMock()
        backend.fetch.side_effect = [OSError("offline"), OSError("still offline"), snapshot]
        self.dock._dashboard._weather._backend = backend
        await self.start("preserved")
        self.dock.open_weather()
        await until(lambda: bool(self.dock.weather.error))
        self.dock.close_popup()
        await render(self.prompt.app)
        self.dock.set_mode(DockMode.BASIC)
        await until(lambda: self.dock.weather.error == "still offline")
        self.assertEqual(backend.fetch.await_count, 2)
        # Redraw, repeated show(), and BASIC/MAXIMIZED changes aren't a reopen.
        self.dock.show()
        self.dock.set_mode(DockMode.MAXIMIZED)
        self.dock.set_mode(DockMode.BASIC)
        for _ in range(3):
            await render(self.prompt.app)
        self.assertEqual(backend.fetch.await_count, 2)
        await self.send(b"\x17")
        self.assertFalse(self.dock.requested_visible)
        await self.send(b"\x17")
        await until(lambda: bool(self.dock.weather.days) and not self.dock.weather.error)
        self.assertEqual(backend.fetch.await_count, 3)
        saved = self.dock.weather
        await self.send(b"\x17")
        await self.send(b"\x17")
        self.assertEqual(backend.fetch.await_count, 3)
        self.assertIs(self.dock.weather, saved)
        self.assertEqual(self.prompt.default_buffer.text, "preserved")

    async def test_calendar_real_keys_save_reopen_and_prompt_preserved(self):
        await self.start("keep this prompt")
        await self.send(b"\x1bc\r")
        page = self.dock._popups.page
        self.assertTrue(page.editing)
        await self.send("Meeting 한글".encode())
        self.assertEqual(page.draft.buffers[EditorField.TITLE].text, "Meeting 한글")
        await self.send(b"\x1bOQ")  # F2
        await until(lambda: page.show_list and not page.busy)
        self.assertEqual(self.dock._calendar.store.load().events[0].title, "Meeting 한글")
        await self.send(b"\x1b")
        await until(lambda: not page.editing)
        await self.send(b"\x1bc")
        await until(lambda: not self.dock._popups.modal)
        self.assertEqual(self.prompt.default_buffer.text, "keep this prompt")
        self.dock.open_calendar()
        await render(self.prompt.app)
        self.assertEqual(self.dock._popups.page.snapshot.on_day(page.selected)[0].title, "Meeting 한글")

    async def test_registered_reminders_save_through_real_keys_and_survive_reopen(self):
        await self.start("unchanged")
        await self.send(b"\x1bc\rMeeting")
        page = self.dock._popups.page
        page.field = EditorField.ALARM_ADD
        await self.send(b"\r")
        page.field = EditorField.ALARM_UNIT
        await self.send(b"\r\x1b[H\r\t\r")  # Choose start, Tab to Add, register.
        self.assertEqual(page.draft.alarms, (Reminder("minutes", 5), Reminder("start", 0)))
        await self.send(b"\x1bOQ")
        await until(lambda: page.show_list and not page.busy)
        self.assertEqual(self.dock._calendar.store.load().events[0].alarms,
                         (Reminder("minutes", 5), Reminder("start", 0)))
        await self.send(b"\x1b")
        await until(lambda: not page.editing)
        await self.send(b"\x1bc")
        await until(lambda: not self.dock._popups.modal)
        self.dock.open_calendar()
        await render(self.prompt.app)
        await self.send(b"\r\r")  # Calendar -> list -> edit selected event.
        self.assertEqual(self.dock._popups.page.draft.alarms, (Reminder("minutes", 5), Reminder("start", 0)))
        self.assertEqual(self.prompt.default_buffer.text, "unchanged")

    async def test_modal_pauses_dock_collectors_and_restores_display_demand(self):
        await self.start()
        self.dock.show()
        await render(self.prompt.app)
        with patch.object(self.dock._audio, "sync") as audio, patch.object(self.dock._media, "sync") as media, \
             patch.object(self.dock._system, "sync") as system, patch.object(self.dock._dashboard, "sync") as dashboard:
            self.dock.open_calendar()
            await render(self.prompt.app)
            self.assertFalse(audio.call_args.kwargs["active"])
            self.assertFalse(media.call_args.kwargs["active"])
            self.assertTrue(system.call_args.kwargs["idle"])
            self.assertFalse(dashboard.call_args.kwargs["dock_active"])
            self.assertTrue(dashboard.call_args.kwargs["clock_active"])
            self.dock.close_popup()
            await render(self.prompt.app)
            self.assertTrue(audio.call_args.kwargs["active"])
            self.assertTrue(media.call_args.kwargs["active"])
            self.assertFalse(system.call_args.kwargs["idle"])

    async def test_calendar_dropdown_dirty_close_confirmation_and_narrow_form(self):
        await self.start()
        self.dock.open_calendar()
        await render(self.prompt.app)
        await self.send(b"\rDraft")
        page = self.dock._popups.page
        page.field = EditorField.REPEAT
        await self.send(b"\r\x1b[B\x1b[B\r")
        self.assertEqual(page.draft.unit, RepeatUnit.WEEKS)
        self.output.columns, self.output.rows = 35, 12
        page.field = EditorField.SAVE
        lines = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("Save" in line for line in lines))
        self.dock.close_popup()
        await render(self.prompt.app)
        self.assertIsNotNone(page.confirm)
        await self.send(b"\t\r")  # Discard
        await until(lambda: not self.dock._popups.modal)
        self.assertFalse(self.dock._calendar.snapshot.events)
        self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_saved_alarm_waits_through_command_and_does_not_take_focus(self):
        config = replace(self.dock.config, popups=replace(self.dock.config.popups,
            calendar=replace(self.dock.config.popups.calendar, poll_s=.01),
            notifications=replace(self.dock.config.popups.notifications, duration_s=None)))
        self.dock.configure(config)
        await self.start()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.dock._calendar.save(CalendarEvent("Due", now + timedelta(hours=1), now + timedelta(hours=2),
                                                      timezone="UTC", alarms=(Reminder.from_delta(now + timedelta(hours=1) - (now + timedelta(seconds=.3))),)))
        self.prompt.app.exit(result="command")
        await self.task
        await until(lambda:self.dock.pending_notifications == 1)
        self.assertFalse(self.dock._popups.notification_visible)
        await self.start("after command")
        await render(self.prompt.app)
        self.assertTrue(self.dock._popups.notification_visible)
        self.assertIs(self.prompt.app.layout.current_control.buffer, self.prompt.default_buffer)
        await self.send(b"abc")
        self.assertIn("abc",self.prompt.default_buffer.text)

    async def test_calendar_mouse_text_input_and_save_button(self):
        self.output.columns, self.output.rows = 150, 50
        self.assertFalse(self.dock.config.dock.mouse.enabled)
        await self.start()
        self.dock.open_calendar()
        # The empty input surface is clickable; New/Events buttons were removed.
        screen = await render(self.prompt.app)
        self.assertTrue(self.prompt.app.mouse_support())
        target = next((x, y) for y, row in screen.data_buffer.items() for x, cell in row.items()
                      if "bg:#475569" in cell.style and cell.char == " ")
        x, y = target
        await self.send(f"\x1b[<0;{x+1};{y+1}M\x1b[<0;{x+1};{y+1}m".encode())
        await render(self.prompt.app)
        page = self.dock._popups.page
        self.assertTrue(page.editing)
        await self.send(b"Mouse event")
        # Use the fragment's public handler for the Korean save glyph; terminal
        # cell splitting is independently covered by the notification test.
        handler = next(p[2] for p in self.dock._popups.layer.general.fragments if len(p)>2 and p[1].strip()=="* Save")
        with set_app(self.prompt.app):
            handler(MouseEvent(Point(0,0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        await until(lambda: page.show_list and not page.busy)
        self.assertEqual(page.snapshot.on_day(page.selected)[0].title,"Mouse event")
        self.assertEqual(self.prompt.default_buffer.text,"draft")

    async def test_notifications_wait_for_prompt_and_pause_while_modal(self):
        self.dock.configure(replace(self.dock.config, popups={"notifications": {"duration_s": .3}}))
        self.dock.notify("First", "Notice")
        self.dock.notify("Second", "Notice")
        await asyncio.sleep(.35)
        self.assertEqual(self.dock.pending_notifications, 2)
        await self.start()
        await until(lambda: self.dock._popups.notification_visible)
        original = self.prompt.app.layout.current_control
        self.dock.open_popup(PopupContent.plain("Modal", "Text"))
        await render(self.prompt.app)
        await asyncio.sleep(.4)
        self.assertEqual(self.dock.pending_notifications, 2)
        self.dock.close_popup()
        await render(self.prompt.app)
        self.assertIs(self.prompt.app.layout.current_control, original)
        await until(lambda: self.dock.pending_notifications == 1)
        self.assertEqual(self.dock._popups.notifications.current.content.title, "Second")
        await until(lambda: self.dock.pending_notifications == 0)

    async def test_notification_keeps_typing_and_opens_details_from_keyboard(self):
        self.dock.configure(replace(self.dock.config, popups={"notifications": {"duration_s": None}}))
        await self.start()
        original = self.prompt.app.layout.current_control
        self.dock.notify("Notice", "Message", details=PopupContent.plain("Details", "Full message"))
        await render(self.prompt.app)
        self.assertIs(self.prompt.app.layout.current_control, original)
        self.assertFalse(self.prompt.app.mouse_support())
        await self.send(b"abc")
        self.assertEqual(self.prompt.default_buffer.text, "draftabc")
        await self.send(b"\x1bn")
        self.assertTrue(self.dock._popups.modal)
        self.assertTrue(self.prompt.app.mouse_support())
        self.assertEqual(self.dock._popups.page.content().title, "Details")
        self.assertEqual(self.dock.pending_notifications, 0)
        self.dock.close_popup()
        self.dock.notify("Dismiss", "Message")
        await render(self.prompt.app)
        self.assertFalse(self.prompt.app.mouse_support())
        await self.send(b"\x1bx")
        self.assertEqual(self.dock.pending_notifications, 0)

    async def test_notification_survives_enter_without_expiring_during_command(self):
        self.dock.configure(replace(self.dock.config, popups={"notifications": {"duration_s": .5}}))
        await self.start("command")
        self.dock.notify("Notice", "Message")
        await render(self.prompt.app)
        self.pipe.send_bytes(b"\r")
        self.assertEqual(await asyncio.wait_for(self.task, 3), "command")
        await asyncio.sleep(.6)
        self.assertEqual(self.dock.pending_notifications, 1)
        await self.start("next")
        self.assertTrue(self.dock._popups.notification_visible)
        await until(lambda: self.dock.pending_notifications == 0)

    async def test_resize_scroll_mouse_close_and_single_float_cleanup(self):
        self.assertFalse(self.dock.config.dock.mouse.enabled)
        await self.start()
        self.dock.open_popup(PopupContent.plain("Long", "\n".join(f"line-{index}" for index in range(70))))
        await render(self.prompt.app)
        self.assertTrue(self.prompt.app.mouse_support())
        self.output.rows, self.output.columns = 8, 28
        await render(self.prompt.app)
        lines = screen_lines(await self.send(b"\x1b[F"))
        self.assertTrue(any("line-69" in line for line in lines), lines)
        self.output.rows, self.output.columns = 30, 100
        screen = await render(self.prompt.app)
        lines = screen_lines(screen)
        y, line = next((y, line) for y, line in enumerate(lines) if "" in line)
        x = line.index("")
        with set_app(self.prompt.app):
            self.prompt.app.renderer.mouse_handlers.mouse_handlers[y][x](MouseEvent(
                Point(x, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        await render(self.prompt.app)
        self.assertFalse(self.dock._popups.modal)
        self.assertFalse(self.prompt.app.mouse_support())
        self.assertEqual(len(self.prompt.floats), 1)
        external = self.prompt.set_float(Label("external"), top=0, left=0)
        uninstall(self.prompt)
        self.assertEqual(self.prompt.floats, [external])

    async def test_modal_stays_above_dock_and_preserves_search_focus(self):
        await self.start("draft")
        self.dock.show()
        await render(self.prompt.app)
        self.prompt.app.layout.focus(self.prompt.search_buffer)
        self.prompt.search_buffer.text = "query"
        previous = self.prompt.app.layout.current_control
        self.dock.open_calendar()
        await render(self.prompt.app)
        self.assertTrue(self.dock.visible)
        self.assertTrue(self.dock._popups.modal)
        self.dock.close_popup()
        await render(self.prompt.app)
        self.assertIs(self.prompt.app.layout.current_control, previous)
        self.assertEqual(self.prompt.search_buffer.text, "query")
        self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_bottom_right_notification_colors_no_footer_and_title_mouse_close(self):
        from prompt_toolkit.styles import Style
        self.dock.configure(replace(self.dock.config, theme={"colors": {"white": "#abcdef", "black": "#112233", "bright_black": "#778899"}}, dock={"mouse": {"enabled": True}}, popups={
            "notifications": {"duration_s": None, "panel": {
                "width": {"value": 40}, "height": {"value": 7},
                "margin": {"right": 3, "bottom": 2},
                "style": {"color": "white", "background": "black", "border_color": "bright_black"}}}}))
        await self.start()
        self.dock.notify("Notice", "Message", details=PopupContent.plain("Details", "Text"))
        screen = await render(self.prompt.app)
        regions = self.dock._popups.layer.mouse_regions
        left, right, _ = regions[0]
        available_h, available_w = self.dock._overlay._available
        self.assertEqual(right, available_w - 3)
        self.assertEqual(right - left, 40)
        self.assertEqual(regions[-1][2], available_h - 2 - 1)
        lines = screen_lines(screen)
        y, line = next((y, line) for y, line in enumerate(lines) if "Message" in line)
        x = line.index("Message")
        attrs = Style([]).get_attrs_for_style_str(screen.data_buffer[y][x].style)
        self.assertEqual((attrs.color, attrs.bgcolor), ("abcdef", "112233"))
        self.assertTrue(any("Alt+N Details" in line and "Alt+X Close" in line for line in lines))
        self.assertEqual(self.dock._popups.layer.notification._actions, ())
        # The body has no hidden details action after removal of its button.
        with set_app(self.prompt.app):
            self.prompt.app.renderer.mouse_handlers.mouse_handlers[y][x](MouseEvent(
                Point(x, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        await render(self.prompt.app)
        self.assertFalse(self.dock._popups.modal)
        self.assertEqual(self.dock.pending_notifications, 1)
        y, line = next((y, line) for y, line in enumerate(lines) if "" in line)
        x = line.index("")
        with set_app(self.prompt.app):
            self.prompt.app.renderer.mouse_handlers.mouse_handlers[y][x](MouseEvent(
                Point(x, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        await render(self.prompt.app)
        self.assertFalse(self.dock._popups.modal)
        self.assertEqual(self.dock.pending_notifications, 0)

    async def test_foreign_focus_is_not_stolen_and_disabling_restores_prompt(self):
        other = TextArea(text="external")
        self.prompt.set_float(other, top=0, left=0, width=30, height=5)
        await self.start()
        self.prompt.app.layout.focus(other)
        self.dock.open_calendar()
        await render(self.prompt.app)
        self.assertIs(self.prompt.app.layout.current_control, other.control)
        self.assertFalse(self.dock._popups.modal)
        self.assertFalse(self.prompt.app.mouse_support())
        self.prompt.app.layout.focus(self.prompt.default_buffer)
        await render(self.prompt.app)
        self.assertTrue(self.dock._popups.modal)
        self.dock.configure(replace(self.dock.config, popups={"calendar": {"enabled": False}}))
        await render(self.prompt.app)
        self.assertFalse(self.dock._popups.modal)
        self.assertFalse(self.prompt.app.mouse_support())
        self.assertIs(self.prompt.app.layout.current_control.buffer, self.prompt.default_buffer)
        self.dock.notify("Queued", "Text")
        self.dock.configure(replace(self.dock.config, popups={"notifications": {"enabled": False}}))
        await render(self.prompt.app)
        self.assertEqual(self.dock.pending_notifications, 0)
