"""Interactive month and event editor, using the calendar runtime's typed API."""
import asyncio
import calendar
from datetime import date
from enum import IntEnum, StrEnum
from ...locales import Locale, Message as M

from ...core.theme import ThemeRole, ThemeColor
from ...popups.models import PopupAction, PopupContent
from ...popups.config import PopupRect
from ...rendering.interactive import InteractivePopup, part, fitted, centered, cropped, button, click
from ...rendering.presentation import TextRow
from ...rendering.message_form import MessageForm
from ...rendering.text import fit, width
from ...rendering.picker import PickerOverlay
from .editor import EditorField, EventDraft
from .models import CalendarEvent, RepeatUnit
from .reminders import ReminderUnit
from .picker_view import DatePicker, TimePicker
from .selection import DateSelection
from .recurrence import RepeatEnd
from .store import CalendarSnapshot
from .contracts import CalendarEntry
from .external_view import detail_rows
from .remote_view import RemoteEditor
from .form_layout import CalendarFormLayout
from .form_view import EventForm
from ...locales.messages import WEEKDAYS


class CalendarColors:
    @staticmethod
    def role(weekday: int, *, holiday=False, default=ThemeRole.FOREGROUND):
        if holiday:
            return ThemeRole.HOLIDAY
        return ThemeRole.SUNDAY if weekday == calendar.SUNDAY else ThemeRole.SATURDAY if weekday == calendar.SATURDAY else default


class Confirmation(StrEnum):
    SAVE = "save"
    DELETE = "delete"

    def message(self, locale):
        return locale.text(M.DELETE_SERIES if self == self.DELETE else M.SAVE_CHANGES)


class DayListFocus(IntEnum):
    EVENTS = 0
    ADD = 1
    EDIT = 2
    DELETE = 3

    def move(self, amount):
        choices = tuple(type(self))
        return choices[(choices.index(self) + amount) % len(choices)]


class CalendarPage(InteractivePopup):
    WEEKDAYS = tuple(day[:3] for day in WEEKDAYS)
    EVENT_ROWS = 4
    EDITOR_PADDING = 1
    TIME_ARROW = CalendarFormLayout.TIME_ARROW
    TIME_GAP = CalendarFormLayout.TIME_GAP
    ALARM_ROWS = EventForm.ALARM_ROWS
    PICKER_BORDER = PickerOverlay.BORDER
    PICKER_MARKER = CalendarFormLayout.PICKER_MARKER

    def __init__(self, clock, config, runtime=None, invalidate=lambda: None, dismiss=lambda: None, *, locale=Locale, calendar=None):
        self._clock, self._config, self.runtime = clock, config, runtime
        self._locale = locale
        self._calendar = calendar
        self._external = None
        self._external_scroll = 0
        self._external_region = None
        self._remote = self._remote_region = None
        self._invalidate, self._dismiss = invalidate, dismiss
        self.selected = clock().now().date()
        self._month = self.selected.replace(day=1)
        self.editing = False
        self.field = EditorField.TITLE
        self.draft = None
        self.event_index = 0
        self.dropdown = None
        self.menu_index = 0
        self.confirm = None
        self.confirm_index = 0
        self._pending = None
        self.error = ""
        self._task = None
        self._scroll = 0
        self._preview_key = None
        self._preview = None
        self._field_rows = {}
        self._field_columns = {}
        self._empty_snapshot = CalendarSnapshot()
        self._month_cache = None
        self._form = EventForm()
        self._message = MessageForm()
        self._message_region = None
        self._alarm_region = None
        self._picker = None
        self._picker_field = None
        self._picker_rect = None
        self._list_region = None
        self._list_offset = 0
        self._list_action = DayListFocus.EVENTS
        self._list_id = None

    @property
    def snapshot(self):
        if self._calendar is not None:
            return self._calendar.snapshot
        return self.runtime.snapshot if self.runtime else self._empty_snapshot

    def _selected_external(self):
        if self._external is not None:
            return self.snapshot.get(self._external)
        if self.draft is None and not self.show_list:
            events = self.snapshot.on_day(self.selected)
            event = events[min(self.event_index, len(events) - 1)] if events else None
            return event if isinstance(event, CalendarEntry) else None
        return None

    @property
    def show_list(self):
        return self.draft is None and self._remote is None and self._external is None and bool(self.snapshot.on_day(self.selected))

    def _can_choose_target(self):
        return self._calendar is not None and bool(self._calendar.edits.destinations)

    def _enter_right(self):
        if self.show_list:
            self.editing = True
        else:
            self._begin()
        self._invalidate()

    def _back_to_day(self):
        self.draft, self._external = None, None
        self.editing = bool(self.snapshot.on_day(self.selected))
        self._list_action = DayListFocus.EVENTS
        self._invalidate()

    @property
    def busy(self):
        return self._task is not None and not self._task.done() or self._remote is not None and self._remote.busy

    def _guard(self, action):
        if self.busy:
            return
        if self._remote is not None:
            def switch():
                self._end_remote()
                action()
                self._invalidate()
            self._remote.leave(switch)
            return
        if self.draft and self.draft.dirty:
            self.confirm, self._pending, self.confirm_index = Confirmation.SAVE, action, 0
        else:
            action()
        self._invalidate()

    def request_close(self):
        if self._remote is not None:
            return self._remote.request_close(self._dismiss)
        if self.busy:
            return False
        self._cancel_picker()
        if self.draft and self.draft.dirty:
            self._guard(self._dismiss)
            return False
        return True

    def dispose(self):
        if self._remote is not None:
            self._remote.dispose()
        self._picker = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _select(self, day):
        def change():
            self.selected, self._month = day, day.replace(day=1)
            self.draft, self.event_index, self._scroll = None, 0, 0
            self.editing = False
            self._external, self._external_scroll = None, 0
            self._list_offset, self._list_action = 0, DayListFocus.EVENTS
            self._list_id = None
        self._guard(change)

    def _move(self, amount):
        selection = DateSelection(self.selected, self._config().week_start)
        selection.months(amount)
        self._select(selection.value)

    def _today(self):
        self._select(self._clock().now().date())

    def _end_remote(self):
        if self._remote is not None:
            self._remote.dispose()
        self._remote, self._external, self.draft = None, None, None
        self.editing = bool(self.snapshot.on_day(self.selected))
        self._invalidate()

    def _remote_form(self):
        def local():
            self._end_remote()
            self._begin(new=True, local=True)
        return RemoteEditor(self._calendar.edits, self.selected, self._clock, self._config, self._locale,
                            self._invalidate, self._end_remote, local, self._picker_day_role)

    def _begin(self, *, new=False, local=False, delete=False):
        self._external, self._external_scroll = None, 0
        if self.draft is None or new:
            events = self._list_events()
            event = self.snapshot.get(events[min(self.event_index, len(events) - 1)].id) if events and not new else None
            if isinstance(event, CalendarEntry):
                self._external, self.editing = event.id, True
                if self._calendar is not None and self._calendar.edits.editable(event.id):
                    self._remote = self._remote_form()
                    self._remote.load(self._calendar.edits.source_for(event.id), event.id[2], delete=delete)
                self._invalidate()
                return
            self.draft = EventDraft(self.selected, getattr(self._clock(), "timezone", None), event,
                                    now=self._clock().now(), choose_target=self._can_choose_target())
        self.editing, self.field = True, EditorField.TITLE
        if delete and self.draft.original is not None:
            self.confirm, self.confirm_index = Confirmation.DELETE, 1
        self._invalidate()

    def _new(self):
        self._guard(lambda: self._begin(new=True))

    def _focus(self, field):
        if self.busy:
            return
        if not self.editing or self.draft is None:
            self._begin()
        if self.draft is None or not self.draft.enabled(field):
            return
        self.field = field
        self._form.reveal_focus()
        self._invalidate()

    def _next_field(self, amount):
        fields = self.draft.fields()
        self.field = fields[(fields.index(self.field) + amount) % len(fields)]
        self._form.reveal_focus()

    def _save(self, *, delete=False):
        if self.busy or self.runtime is None or self.draft is None or self._external is not None:
            return
        try:
            event = self.draft.original if delete else self.draft.event()
            if event is None:
                return
        except ValueError as exc:
            self.error = str(exc)
            return
        async def save():
            try:
                if delete:
                    await self.runtime.delete(event)
                    self.draft = None
                    self.editing = False
                else:
                    saved = await self.runtime.save(event)
                    self.draft = EventDraft(self.selected, saved.timezone, saved)
                self.error = ""
                self._task = None
                pending, self._pending, self.confirm = self._pending, None, None
                if pending:
                    pending()
                else:
                    self._back_to_day()
            except Exception as exc:
                self.error = str(exc) or type(exc).__name__
                self.confirm, self._pending = None, None
            finally:
                self._invalidate()
        self._task = asyncio.create_task(save())

    def _activate(self):
        if self.busy:
            return
        field = self.field
        if field == EditorField.TARGET:
            # Destination selection precedes editing. Never discard an existing draft silently.
            self._guard(self._choose_destination)
        elif field.temporal_type is not None:
            self._open_picker(field)
        elif field == EditorField.SAVE:
            self._save()
        elif field == EditorField.CANCEL:
            self._guard(self._back_to_day)
        elif field == EditorField.DELETE and self.draft.original:
            self.confirm, self.confirm_index = Confirmation.DELETE, 1
        elif field == EditorField.ALL_DAY:
            self.draft.toggle_all_day()
        elif field == EditorField.HOLIDAY:
            self.draft.holiday = not self.draft.holiday
        elif field == EditorField.REPEAT:
            self.dropdown = "repeat"
            self.menu_index = tuple(RepeatUnit).index(self.draft.unit)
        elif field == EditorField.REPEAT_END:
            self.dropdown = "repeat_end"
            self.menu_index = tuple(RepeatEnd).index(self.draft.repeat_end)
        elif field == EditorField.ALARM_UNIT:
            self.dropdown = "alarm"
            self.menu_index = tuple(ReminderUnit).index(self.draft.alarm_unit)
        elif field == EditorField.ALARM_ADD:
            try:
                self.draft.add_alarm()
                self.error = ""
            except ValueError as exc:
                self.error = str(exc)
        else:
            self._next_field(1)

    def _choose_destination(self):
        self.draft = None
        self._remote, self.editing = self._remote_form(), True
        self._invalidate()

    def _cancel_picker(self):
        self._picker = self._picker_field = self._picker_rect = None
        self._invalidate()

    def _dropdown_choices(self):
        if self.dropdown == "repeat":
            return tuple(RepeatUnit)
        if self.dropdown == "repeat_end":
            return tuple(RepeatEnd)
        if self.dropdown == "alarm":
            return tuple(ReminderUnit)
        return self.snapshot.on_day(self.selected)

    def _dropdown_field(self):
        return {"repeat": EditorField.REPEAT, "repeat_end": EditorField.REPEAT_END,
                "alarm": EditorField.ALARM_UNIT}.get(self.dropdown)

    def _apply_picker(self, value):
        if self.draft is not None and self._picker_field is not None:
            self.draft.choose_temporal(self._picker_field, value)
            self.field = self._picker_field
            self.error = ""
        self._cancel_picker()

    def _picker_day_role(self, *, day=None, weekday=None):
        if day is None:
            return CalendarColors.role(weekday, default=ThemeRole.MENU_TEXT)
        return CalendarColors.role(day.weekday(), holiday=any(item.holiday for item in self.snapshot.on_day(day)),
                                   default=ThemeRole.MENU_TEXT)

    def _open_picker(self, field):
        if self.draft is None or not self.draft.enabled(field):
            return
        try:
            value = self.draft.temporal_value(field)
        except ValueError as exc:
            self.error = str(exc)
            return
        self.dropdown = None
        self._picker_field = field
        if field.temporal_type is date:
            self._picker = DatePicker(value, self._config().week_start, lambda: self._clock().now().date(),
                                      self._apply_picker, self._cancel_picker, self._invalidate, self._picker_day_role)
        else:
            self._picker = TimePicker(value, self._apply_picker, self._cancel_picker, self._invalidate)
        self.error = ""
        self._invalidate()

    def _scroll_alarms(self, amount):
        self._focus(EditorField.ALARMS)
        self._form.alarm_offset = max(0, min(max(0, len(self.draft.alarms) - self.ALARM_ROWS),
                                        self._form.alarm_offset + amount))
        self.draft.select_alarm(max(self._form.alarm_offset, min(self.draft.alarm_index,
                                                          self._form.alarm_offset + self.ALARM_ROWS - 1)))
        self._invalidate()

    def scroll_at(self, x, y, amount):
        if self.confirm:
            if self._message_region is not None and self._message_region.contains(x, y):
                self._message.scroll(amount)
                self._invalidate()
            return True
        if self._list_region is not None and self._list_region.contains(x, y) and self.show_list:
            self._list_select(self.event_index + amount)
            return True
        if self._remote is not None and self._remote_region is not None and self._remote_region.contains(x, y):
            return self._remote.scroll_at(x - self._remote_region.left, y - self._remote_region.top, amount)
        if self._external_region is not None and self._external_region.contains(x, y):
            self._external_scroll = max(0, self._external_scroll + amount)
            self._invalidate()
            return True
        if self._picker is not None:
            if self._picker_rect is not None and self._picker_rect.contains(x, y):
                self._picker.scroll_at(x - self._picker_rect.left, y - self._picker_rect.top, amount)
            return True
        if not self.busy and not self.dropdown and self._alarm_region is not None and self._alarm_region.contains(x, y):
            self._scroll_alarms(amount)
            return True
        return False

    def key(self, key, data):
        if key == "f5" and self._calendar is not None:
            self._calendar.refresh()
            return True
        if self._remote is not None:
            return self._remote.key(key, data)
        if self.busy:
            return True
        if self._picker is not None:
            return self._picker.key(key, data)
        if self.confirm:
            choices = 2 if self.confirm == Confirmation.DELETE else 3
            if self._message.key(key):
                pass
            elif key in ("tab", "right", "s-tab", "left"):
                self.confirm_index = (self.confirm_index + (-1 if key in ("s-tab", "left") else 1)) % choices
            elif key == "escape":
                self.confirm, self._pending = None, None
            elif key == "enter" or data == " ":
                if self.confirm == Confirmation.DELETE:
                    if self.confirm_index == 0:
                        self._save(delete=True)
                    self.confirm = None
                elif self.confirm_index == 0:
                    self._save()
                elif self.confirm_index == 1:
                    action, self._pending, self.confirm, self.draft = self._pending, None, None, None
                    if action:
                        action()
                else:
                    self.confirm, self._pending = None, None
            return True
        if self.dropdown:
            choices = self._dropdown_choices()
            if key in ("up", "down", "pageup", "pagedown", "home", "end"):
                self.menu_index = (0 if key == "home" else len(choices) - 1 if key == "end" else
                    max(0, min(len(choices) - 1, self.menu_index + {"up": -1, "down": 1, "pageup": -4, "pagedown": 4}[key])))
            elif key == "escape":
                self.dropdown = None
            elif key in ("enter", "tab", "s-tab") or data == " ":
                if choices:
                    if self.dropdown == "repeat":
                        self.draft.unit = choices[self.menu_index]
                    elif self.dropdown == "repeat_end":
                        self.draft.repeat_end = choices[self.menu_index]
                    elif self.dropdown == "alarm":
                        self.draft.alarm_unit = choices[self.menu_index]
                    else:
                        self.event_index, self.draft = self.menu_index, None
                        self._begin()
                self.dropdown = None
                if key in ("tab", "s-tab") and self.draft is not None:
                    self._next_field(-1 if key == "s-tab" else 1)
            return True
        if key in ("scroll-up", "scroll-down"):
            change = -3 if key == "scroll-up" else 3
            self._scroll = max(0, self._scroll + change)
            return True
        if self.editing:
            if self.show_list:
                self._list_key(key, data)
                return True
            if self.draft is None and self._external is None:
                self._begin(new=True)
            if self._external is not None:
                if key == "escape":
                    self.editing, self._external = False, None
                elif data == "n":
                    self._new()
                elif data == "e":
                    self.dropdown, self.menu_index = "events", 0
                elif key in ("up", "down", "pageup", "pagedown"):
                    self._external_scroll = max(0, self._external_scroll + {"up": -1, "down": 1, "pageup": -8, "pagedown": 8}[key])
                return True
            if key == "escape":
                self._guard(self._back_to_day)
            elif key == "f2":
                self._save()
            elif key in ("tab", "s-tab"):
                self._next_field(-1 if key == "s-tab" else 1)
            elif self.field == EditorField.ALARMS and key in ("up", "down", "pageup", "pagedown", "home", "end", "delete", "backspace", "c-h"):
                if key in ("delete", "backspace", "c-h"):
                    self.draft.remove_alarm()
                else:
                    self.draft.select_alarm(0 if key == "home" else len(self.draft.alarms) - 1 if key == "end"
                                           else self.draft.alarm_index + {"up": -1, "down": 1,
                                                                         "pageup": -self.ALARM_ROWS, "pagedown": self.ALARM_ROWS}[key])
            elif self.draft.edit(self.field, key, data):
                pass
            elif key == "enter" or data == " ":
                self._activate()
            return True
        if key in ("left", "right", "up", "down"):
            selection = DateSelection(self.selected, self._config().week_start)
            selection.days({"left": -1, "right": 1, "up": -selection.DAYS_PER_WEEK, "down": selection.DAYS_PER_WEEK}[key])
            self._select(selection.value)
        elif key == "pageup":
            self._move(-1)
        elif key == "pagedown":
            self._move(1)
        elif key == "home":
            self._today()
        elif key in ("enter", "tab") or data == " ":
            self._enter_right()
        elif data == "n":
            self._new()
        elif data == "e":
            self._enter_right()
        elif key == "escape":
            return False
        return True

    def content(self):
        locale = self._locale()
        return PopupContent(locale.text(M.CALENDAR), (TextRow.plain(locale.text(M.CALENDAR), ""),), (
            PopupAction(locale.text(M.PREVIOUS_MONTH), lambda: self._move(-1)), PopupAction(locale.text(M.TODAY), self._today),
            PopupAction(locale.text(M.NEXT_MONTH), lambda: self._move(1))))

    def status_text(self):
        return self._status_text()

    def _status_text(self):
        locale = self._locale()
        if self._remote is not None:
            return self._remote.status_text()
        if self.confirm and not self.busy:
            return self._message.status_text(locale)
        message = self.error or (self.runtime.error if self.runtime else "")
        if message:
            return locale.text(M.ERROR_HINT, message=message)
        if self.busy:
            return locale.text(M.SAVING)
        if self._picker is not None:
            return self._picker.status_text(locale)
        if self.dropdown:
            return locale.text(M.MENU_HINT)
        if not self.editing:
            return locale.text(M.CALENDAR_HINT)
        if self.show_list:
            return locale.text(M.CALENDAR_LIST_HINT)
        if self._external is not None:
            return locale.text(M.CALENDAR_EXTERNAL_HINT)
        if self.draft is None:
            return locale.text(M.CALENDAR_HINT)
        buffer = self.draft.buffer(self.field)
        action = locale.text(M.OPEN_PICKER if self.field.temporal_type is not None else M.ALARM_LIST_HINT if self.field == EditorField.ALARMS else M.MULTILINE_HINT
                            if self.field == EditorField.NOTES else M.TEXT_HINT if buffer is not None else M.ACTION_HINT)
        return locale.text(M.EDITOR_HINT, field=self.field.translated(locale), action=action)

    def _month_rows(self, cells, theme):
        locale = self._locale()
        today = self._clock().now().date()
        key = (cells, theme, locale, self.snapshot, self._month, self.selected, today,
               self._config().week_start, CalendarEvent.recurrence_context())
        if self._month_cache is not None and self._month_cache[0] == key:
            return self._month_cache[1]
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **kw)
        month = DateSelection(self._month, self._config().week_start)
        weekdays = month.weekdays
        nav = [p(" < ", ThemeColor.WHITE, background=ThemeRole.BUTTON_BG, action=lambda: self._move(-1)),
               p(" " + locale.month(self._month.month) + f" {self._month.year} ", bold=True),
               p(" > ", ThemeColor.WHITE, background=ThemeRole.BUTTON_BG, action=lambda: self._move(1))]
        tail = locale.text(M.TODAY) + " " + today.isoformat()
        nav += [p(" " * max(0, cells - sum(width(item[1]) for item in nav) - width(tail))), p(tail, ThemeRole.MUTED, action=self._today)]
        rows = [fitted(nav, cells, theme), fitted([], cells, theme)]
        cell = max(1, cells // len(weekdays))
        rows.append([item for weekday in weekdays for item in centered([p(locale.weekday(weekday, abbreviated=True), CalendarColors.role(weekday), bold=True)], cell, theme)])
        selected_row = 3
        snapshot = self.snapshot
        for days in month.weeks:
            rows.append([p("─" * cells, ThemeRole.SEPARATOR)])
            events_by_day = {day: snapshot.on_day(day) for day in days if day is not None}
            for offset in range(self.EVENT_ROWS + 1):
                row = []
                for weekday, day in zip(weekdays, days):
                    if day is None:
                        row.extend(fitted([], cell, theme))
                        continue
                    events = events_by_day[day]
                    selected = day == self.selected
                    if selected and offset == 0:
                        selected_row = len(rows)
                    role = CalendarColors.role(weekday, holiday=any(e.holiday for e in events))
                    event = events[offset - 1] if 0 < offset <= len(events) else None
                    caption = event.caption if isinstance(event, CalendarEntry) else event.title if event else ""
                    text = f" {day.day:02}" + (" •" if day == today else "") if offset == 0 else " " + caption
                    if offset == self.EVENT_ROWS and len(events) > self.EVENT_ROWS:
                        text = " " + locale.text(M.MORE_EVENTS, count=len(events) - self.EVENT_ROWS + 1)
                    if offset:
                        role = ThemeRole.TITLE if selected else ThemeRole.MUTED
                    label = fit(text, cell)
                    row.append(p(label + " " * max(0, cell - width(label)), role, background=ThemeRole.BUTTON_BG if selected else ThemeRole.BACKGROUND,
                                 bold=offset == 0, action=lambda day=day: self._select(day)))
                rows.append(fitted(row, cells, theme))
        result = rows, selected_row
        self._month_cache = key, result
        return result

    def _list_events(self):
        events = self.snapshot.on_day(self.selected)
        if events:
            self.event_index = next((index for index, event in enumerate(events) if event.id == self._list_id),
                                    max(0, min(self.event_index, len(events) - 1)))
            self._list_id = events[self.event_index].id
        else:
            self.event_index, self._list_id = 0, None
        return events

    def _list_select(self, index):
        events = self.snapshot.on_day(self.selected)
        self.event_index = max(0, min(len(events) - 1, index))
        self._list_id = events[self.event_index].id if events else None
        self.editing, self._list_action = True, DayListFocus.EVENTS
        self._invalidate()

    def _list_editable(self):
        events = self._list_events()
        if not events:
            return False
        event = events[min(self.event_index, len(events) - 1)]
        return not isinstance(event, CalendarEntry) or self._calendar is not None and self._calendar.edits.editable(event.id)

    def _list_edit(self, *, delete=False):
        if self.busy or not self._list_editable():
            return
        self._begin(delete=delete)

    def _list_key(self, key, data):
        events = self._list_events()
        if key == "escape":
            self.editing = False
        elif key in ("up", "down", "pageup", "pagedown", "home", "end"):
            self._list_select(0 if key == "home" else len(events) - 1 if key == "end" else
                self.event_index + {"up": -1, "down": 1, "pageup": -4, "pagedown": 4}[key])
        elif key in ("tab", "s-tab"):
            self._list_action = self._list_action.move(-1 if key == "s-tab" else 1)
        elif key == "delete":
            self._list_edit(delete=True)
        elif data == "n":
            self._new()
        elif data == "e":
            self._list_edit()
        elif key == "enter" or data == " ":
            if self._list_action == DayListFocus.ADD:
                self._new()
            elif self._list_action == DayListFocus.DELETE:
                self._list_edit(delete=True)
            elif self._list_editable():
                self._list_edit()
            else:
                self._begin()  # Read-only external details remain readable.
        self._invalidate()

    def _list_rows(self, cells, height, theme):
        locale, bg = self._locale(), ThemeRole.EDITOR_BG
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **{"background": bg, **kw})
        fill = lambda items=(): fitted(items, cells, theme, background=bg)
        events = self._list_events()
        self.event_index = min(self.event_index, len(events) - 1)
        editable = self._list_editable()
        header = [fill(), fill([p(locale.text(M.CALENDAR_EVENT_LIST), ThemeRole.MUTED)]), fill()]
        actions = [button(theme, locale.text(M.CALENDAR_ADD_EVENT), role=ThemeRole.SAVE,
                          selected=self.editing and self._list_action == DayListFocus.ADD, action=self._new), p("   "),
                   button(theme, locale.text(M.CALENDAR_EDIT),
                          selected=self.editing and self._list_action == DayListFocus.EDIT,
                          role=ThemeRole.FOREGROUND if editable else ThemeRole.MUTED,
                          action=self._list_edit if editable else None), p("   "),
                   button(theme, locale.text(M.DELETE),
                          selected=self.editing and self._list_action == DayListFocus.DELETE,
                          role=ThemeRole.DELETE if editable else ThemeRole.MUTED,
                          action=(lambda: self._list_edit(delete=True)) if editable else None)]
        footer = [fill(), centered(actions, cells, theme, background=bg), fill()]
        room = max(0, height - len(header) - len(footer))
        items = []
        for index, event in enumerate(events):
            active = index == self.event_index
            role, background = (ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if active else (ThemeRole.FOREGROUND, ThemeRole.INPUT_BG)
            select = lambda index=index: self._list_select(index)
            title = fit(("› " if active else "  ") + event.title, cells)
            items.append(fill([p(title + " " * max(0, cells - width(title)), role, background=background, action=select)]))
            stamp = locale.text(M.ALL_DAY) if event.all_day else event.start.strftime("%H:%M") + " – " + event.end.strftime("%H:%M")
            source = event.source if isinstance(event, CalendarEntry) else locale.text(M.CALENDAR_LOCAL)
            text = fit("  " + stamp + " · " + source, cells)
            items.append(fill([p(text + " " * max(0, cells - width(text)), role if active else ThemeRole.MUTED,
                                 background=background, action=select)]))
            items.append(fill())
        target = self.event_index * 3
        self._list_offset = max(0, min(self._list_offset, target))
        self._list_offset = max(self._list_offset, target + min(2, room) - room)
        visible = items[self._list_offset:self._list_offset + room]
        return (header + visible + [fill() for _ in range(room - len(visible))] + footer)[:height]

    def _editor_rows(self, cells, height, theme):
        if height <= 0:
            return []
        padding = min(self.EDITOR_PADDING, max(0, (cells - 1) // 2))
        content_width = cells - 2 * padding
        heading = (None if self.confirm else self._remote.heading_date if self._remote is not None else
                   None if self._selected_external() is not None or self._external is not None else self.selected)
        header = CalendarFormLayout.heading(heading, content_width, theme)
        content_height = max(0, height - CalendarFormLayout.HEADER_ROWS)
        if not content_height:
            self._field_rows, self._field_columns = {}, {}
        rows = self._editor_content_rows(content_width, content_height, theme) if content_height else []
        self._field_rows = {field: row + CalendarFormLayout.HEADER_ROWS for field, row in self._field_rows.items()}
        self._field_columns = {field: column + padding for field, column in self._field_columns.items()}
        if self._alarm_region is not None:
            region = self._alarm_region
            self._alarm_region = PopupRect(region.left + padding, region.top + CalendarFormLayout.HEADER_ROWS, region.width, region.height)
        space = part(theme, " " * padding, background=ThemeRole.EDITOR_BG)
        return [[space, *fitted(row, content_width, theme, background=ThemeRole.EDITOR_BG), space] for row in [header, *rows]]

    def _editor_content_rows(self, cells, height, theme):
        locale = self._locale()
        if self.confirm:
            self._field_rows, self._field_columns = {}, {}
            return self._confirmation_rows(cells, height, theme)
        if self.show_list:
            self._field_rows, self._field_columns = {}, {}
            return self._list_rows(cells, height, theme)
        if self._remote is not None:
            self._field_rows, self._field_columns = {}, {}
            return self._remote.draw(cells, height, theme)
        external = self._selected_external()
        if external is not None or self._external is not None:
            self._field_rows, self._field_columns = {}, {}
            return detail_rows(external, cells, height, theme, locale,
                               getattr(self._clock(), "timezone", None), self._external_scroll)
        draft = self.draft
        if draft is None:
            key = (self.selected, getattr(self._clock(), "timezone", None), self._can_choose_target())
            if key != self._preview_key:
                self._preview_key = key
                self._preview = EventDraft(self.selected, key[1], now=self._clock().now(), choose_target=key[2])
            draft = self._preview
        def select_alarm(index):
            self._focus(EditorField.ALARMS)
            self.draft.select_alarm(index)
            self._invalidate()
        def remove_alarm(index):
            self._focus(EditorField.ALARMS)
            self.draft.remove_alarm(index)
            self._invalidate()
        def activate(field):
            self._focus(field)
            self._activate()
        rows = self._form.draw(draft, self.selected, self.field, self.editing, cells, height, theme, locale,
            focus=self._focus, activate=activate, open_picker=self._open_picker, changed=self._invalidate,
            target_label=locale.text(M.CALENDAR_LOCAL) if self._can_choose_target() else "",
            on_alarm_select=select_alarm, on_alarm_remove=remove_alarm)
        self._field_rows, self._field_columns = self._form.field_rows, self._form.field_columns
        self._alarm_region = self._form.alarm_region
        return rows

    def _confirmation_rows(self, cells, height, theme):
        labels = (M.DELETE, M.CANCEL) if self.confirm == Confirmation.DELETE else (M.SAVE, M.DISCARD, M.CANCEL)
        choices = []
        for index, label in enumerate(labels):
            def choose(index=index):
                self.confirm_index = index
                self.key("enter", "")
                self._invalidate()
            choices.append(PopupAction(self._locale().text(label), choose))
        details = [TextRow.plain(self.error, ThemeRole.ERROR)] if self.error else []
        return self._message.draw(TextRow.plain(self.confirm.message(self._locale()), ThemeRole.WARNING),
            cells, height, theme, details=details, actions=choices, selected=self.confirm_index,
            heading=None, background=ThemeRole.EDITOR_BG)

    def draw_body(self, columns, rows, theme):
        self._message_region = None
        self._alarm_region = None
        self._picker_rect = None
        self._external_region = None
        self._remote_region = None
        self._list_region = None
        if columns <= 0 or rows <= 0:
            return []
        padding = int(rows > 1)
        body = self._draw_body(columns, rows - padding, theme)
        # Reserve a full-width row above the shared status bar, outside both panes.
        return body + [fitted([], columns, theme) for _ in range(rows - len(body))]

    def _draw_body(self, columns, rows, theme):
        if columns <= 0 or rows <= 0:
            return []
        p = lambda text, role=ThemeRole.FOREGROUND, **kw: part(theme, text, role, **kw)
        split = columns >= 70
        left_w = (columns - 3) * 2 // 3 if split else columns
        right_w = columns - left_w - 3 if split else columns
        left, selected_row = self._month_rows(left_w, theme) if split or not self.editing else ([], 0)
        right = self._editor_rows(right_w, rows, theme) if split or self.editing else []
        if self.confirm and (split or self.editing):
            self._message_region = PopupRect(left_w + 3 if split else 0, CalendarFormLayout.HEADER_ROWS,
                right_w, max(0, rows - CalendarFormLayout.HEADER_ROWS))
        if self.show_list and (split or self.editing):
            self._list_region = PopupRect(left_w + 3 if split else 0, CalendarFormLayout.HEADER_ROWS, right_w, max(0, rows - CalendarFormLayout.HEADER_ROWS))
        if self._remote is not None and (split or self.editing):
            padding = min(self.EDITOR_PADDING, max(0, (right_w - 1) // 2))
            self._remote_region = PopupRect((left_w + 3 if split else 0) + padding, CalendarFormLayout.HEADER_ROWS, right_w - padding * 2, max(0, rows - CalendarFormLayout.HEADER_ROWS))
        elif (split or self.editing) and (self._external is not None or self._selected_external() is not None):
            self._external_region = PopupRect(left_w + 3 if split else 0, CalendarFormLayout.HEADER_ROWS, right_w, max(0, rows - CalendarFormLayout.HEADER_ROWS))
        if split and self._alarm_region is not None:
            region = self._alarm_region
            self._alarm_region = PopupRect(left_w + 3 + region.left, region.top, region.width, region.height)
        available = rows
        left_start = max(0, selected_row - available + self.EVENT_ROWS + 1) if len(left) > available else 0
        left_start = min(max(0, len(left) - available), left_start + self._scroll)
        body = []
        for index in range(available):
            l = left[index + left_start] if index + left_start < len(left) else []
            if self.confirm:
                l = [item[:2] for item in l]
            r = right[index] if index < len(right) else []
            body.append([*fitted(l, left_w, theme), p(" │ ", ThemeRole.SEPARATOR), *fitted(r, right_w, theme, background=ThemeRole.EDITOR_BG)] if split else fitted(r if self.editing else l, columns, theme))
        if self._picker is not None:
            return self._draw_picker(body, columns, rows, theme, left_w + 3 if split else 0)
        if self.dropdown:
            choices = ([item.caption if isinstance(item, CalendarEntry) else item.title for item in self._dropdown_choices()] if self.dropdown == "events" else
                       [item.translated(self._locale()) for item in self._dropdown_choices()])
            choices = choices or [self._locale().text(M.NO_EVENTS)]
            self.menu_index = min(self.menu_index, len(choices) - 1)
            size = min(len(choices), max(1, rows - CalendarFormLayout.HEADER_ROWS))
            start = max(0, min(self.menu_index - size + 1, len(choices) - size))
            menu_w = min(columns, max(12, max(width(label) for label in choices) + 3))
            origin = left_w + 3 if split else 0
            menu_field = self._dropdown_field()
            field_x = self._field_columns.get(menu_field, 0)
            anchor_x = min(max(0, columns - menu_w), origin + field_x)
            anchor_y = self._field_rows.get(menu_field, 0) + 1 if menu_field else 2
            anchor_y = max(min(CalendarFormLayout.HEADER_ROWS, rows - size), min(rows - size, anchor_y))
            for index, label in enumerate(choices[start:start + size]):
                selected = index + start == self.menu_index
                def choose(index=index):
                    self.menu_index = index + start
                    self.key("enter", "")
                    self._invalidate()
                role, bg = (ThemeRole.SELECTED_FG, ThemeRole.SELECTED_BG) if selected else (ThemeRole.MENU_TEXT, ThemeRole.BUTTON_BG)
                label = fit(" " + label, menu_w - 1)
                row = [p(label + " " * max(0, menu_w - 1 - width(label)), role, background=bg, action=choose),
                       p("█" if selected else " ", ThemeRole.SCROLLBAR, background=ThemeRole.BUTTON_BG)]
                target = anchor_y + index
                body[target] = [*cropped(body[target], 0, anchor_x, theme), *row,
                                *cropped(body[target], anchor_x + menu_w, columns - anchor_x - menu_w, theme)]
        return body[:rows]

    def _draw_picker(self, body, columns, rows, theme, origin):
        heading_rows = min(CalendarFormLayout.HEADER_ROWS, rows)
        if rows <= heading_rows:
            return body
        title = [[(item[0], item[1], click(self._cancel_picker)) for item in row] for row in body[:heading_rows]]
        content, rect = PickerOverlay.draw(body[heading_rows:], columns, rows - heading_rows, self._picker,
            (origin + self._field_columns.get(self._picker_field, 0), self._field_rows.get(self._picker_field, 0) - heading_rows),
            theme, self._locale(), self._cancel_picker)
        self._picker_rect = PopupRect(rect.left, rect.top + heading_rows, rect.width, rect.height)
        return [*title, *content][:rows]
