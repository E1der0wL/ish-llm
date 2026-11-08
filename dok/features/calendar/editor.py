"""Local calendar draft; persisted validation is owned by CalendarEvent."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from .models import CalendarEvent, Recurrence, RepeatUnit, LocalDateTime
from .reminders import Reminder, ReminderUnit
from .recurrence import RepeatEnd
from .timing import EventTiming
from ...locales import Locale, Message as M


class EditorField(StrEnum):
    TITLE = "title"
    ALL_DAY = "all_day"
    START_DATE = "start_date"
    END_DATE = "end_date"
    START_TIME = "start_time"
    END_TIME = "end_time"
    INTERVAL = "interval"
    REPEAT = "repeat"
    HOLIDAY = "holiday"
    REPEAT_COUNT = "repeat_count"
    REPEAT_UNTIL = "repeat_until"
    REPEAT_END = "repeat_end"
    ALARM_NUMBER = "alarm_number"
    ALARM_UNIT = "alarm_unit"
    ALARM_ADD = "alarm_add"
    ALARMS = "alarms"
    COMMAND = "command"
    NOTES = "notes"
    SAVE = "save"
    CANCEL = "cancel"
    DELETE = "delete"

    @property
    def label(self):
        return self.translated(Locale())

    @property
    def temporal_type(self):
        if self in (self.START_DATE, self.END_DATE, self.REPEAT_UNTIL):
            return date
        if self in (self.START_TIME, self.END_TIME):
            return time
        return None

    def translated(self, locale):
        return locale.text({self.TITLE: M.TITLE, self.ALL_DAY: M.TIME, self.START_DATE: M.START_DATE, self.END_DATE: M.END_DATE,
                self.START_TIME: M.START_TIME, self.END_TIME: M.END_TIME,
                self.COMMAND: M.COMMAND, self.ALARM_NUMBER: M.ALARM_NUMBER, self.ALARM_UNIT: M.ALARM_UNIT,
                self.ALARM_ADD: M.REGISTER, self.ALARMS: M.ALARM, self.INTERVAL: M.INTERVAL,
                self.REPEAT: M.REPEAT, self.HOLIDAY: M.HOLIDAY, self.NOTES: M.NOTES,
                self.REPEAT_END: M.REPEAT_LIMIT, self.REPEAT_COUNT: M.REPEAT_COUNT_MODE, self.REPEAT_UNTIL: M.REPEAT_DATE_MODE,
                self.SAVE: M.SAVE, self.CANCEL: M.CANCEL, self.DELETE: M.DELETE}[self])


class EventDraft:
    def __init__(self, day, timezone, event=None, *, now=None):
        self.original = event
        start, end = (event.start, event.end) if event else EventTiming.initial(day, timezone, now)
        self.timezone = event.timezone if event else timezone
        self.all_day = event.all_day if event else False
        self.holiday = event.holiday if event else False
        repeat = event.repeat if event else Recurrence()
        self.unit, self.repeat_end = repeat.unit, repeat.end
        alarm = Reminder()
        self.alarm_unit = alarm.unit
        self.alarms = event.alarms if event else ()
        self.alarm_index = 0
        visible_end = end - timedelta(days=1) if self.all_day else end
        values = {EditorField.TITLE: event.title if event else "",
                  EditorField.START_DATE: start.date().isoformat(), EditorField.END_DATE: visible_end.date().isoformat(),
                  EditorField.START_TIME: self._temporal_text(start.time()),
                  EditorField.END_TIME: self._temporal_text(end.time()),
                  EditorField.COMMAND: event.command if event else "",
                  EditorField.REPEAT_COUNT: str(repeat.count),
                  EditorField.REPEAT_UNTIL: (repeat.until or start.date()).isoformat(),
                  EditorField.ALARM_NUMBER: f"{alarm.value:g}",
                  EditorField.INTERVAL: str(repeat.interval), EditorField.NOTES: event.notes if event else ""}
        self.buffers = {field: Buffer(document=Document(text, len(text)), multiline=field == EditorField.NOTES) for field, text in values.items()}
        self._initial = self.signature

    @property
    def signature(self):
        return (tuple((key, buffer.text) for key, buffer in self.buffers.items() if key != EditorField.ALARM_NUMBER),
                self.all_day, self.holiday, self.unit, self.repeat_end, self.alarms)

    @property
    def dirty(self):
        return self.signature != self._initial

    def buffer(self, field):
        """Return the text editing API for a field, or None for a button."""
        return self.buffers.get(field) if self.enabled(field) else None

    def repeat_count_display(self, locale):
        buffer = self.buffers[EditorField.REPEAT_COUNT]
        return Recurrence.count_display(buffer.text, buffer.cursor_position, locale)

    def enabled(self, field):
        return not (self.all_day and field in (EditorField.START_TIME, EditorField.END_TIME)
                    or field == EditorField.ALARM_NUMBER and not self.alarm_unit.uses_number
                    or field in (EditorField.REPEAT_END, EditorField.REPEAT_COUNT, EditorField.REPEAT_UNTIL) and self.unit == RepeatUnit.NONE
                    or field == EditorField.REPEAT_COUNT and self.repeat_end != RepeatEnd.COUNT
                    or field == EditorField.REPEAT_UNTIL and self.repeat_end != RepeatEnd.DATE)

    def fields(self):
        """Visible focus order; hidden time inputs cannot receive focus."""
        return tuple(field for field in EditorField if self.enabled(field))

    @property
    def alarm_number(self):
        return self.buffers[EditorField.ALARM_NUMBER]

    def add_alarm(self):
        alarm = Reminder.from_input(self.alarm_unit, self.alarm_number.text)
        self.alarms = Reminder.ordered((*self.alarms, alarm))
        self.alarm_index = next(index for index, item in enumerate(self.alarms) if item.identity == alarm.identity)

    def select_alarm(self, index):
        self.alarm_index = max(0, min(len(self.alarms) - 1, index))

    def remove_alarm(self, index=None):
        index = self.alarm_index if index is None else index
        self.alarms = tuple(item for position, item in enumerate(self.alarms) if position != index)
        self.select_alarm(index)

    def toggle_all_day(self):
        self.all_day = not self.all_day

    @staticmethod
    def _temporal_text(value: date | time) -> str:
        if isinstance(value, time):
            if value.tzinfo is not None:
                raise ValueError("Expected a local time without a UTC offset")
            return value.isoformat(timespec="auto" if value.second or value.microsecond else "minutes")
        return value.isoformat()

    def temporal_value(self, field: EditorField) -> date | time:
        value_type = field.temporal_type
        if value_type is None:
            raise ValueError("Expected a date or time field")
        return value_type.fromisoformat(self.buffers[field].text)

    def temporal_label(self, field: EditorField) -> str:
        value = self.temporal_value(field)
        return value.isoformat(timespec="seconds") if field.temporal_type is time else value.isoformat()

    def choose_temporal(self, field: EditorField, value: date | time) -> None:
        """Apply a confirmed picker value; the event owns cross-field validation."""
        if field.temporal_type is None or type(value) is not field.temporal_type:
            raise ValueError("The selected value does not match this field")
        if not self.enabled(field):
            return
        text = self._temporal_text(value)
        self.buffers[field].document = Document(text, len(text))

    def event(self):
        def text(field):
            return self.buffers[field].text
        start_day, end_day = self.temporal_value(EditorField.START_DATE), self.temporal_value(EditorField.END_DATE)
        if self.all_day:
            # The editor shows inclusive dates; storage owns exclusive end times.
            start = datetime.combine(start_day, time())
            try:
                end = datetime.combine(end_day + timedelta(days=1), time())
            except OverflowError as exc:
                raise ValueError("All-day end cannot extend beyond Python's supported date range") from exc
        else:
            start = LocalDateTime().decode(datetime.combine(start_day, self.temporal_value(EditorField.START_TIME)))
            end = LocalDateTime().decode(datetime.combine(end_day, self.temporal_value(EditorField.END_TIME)))
        event = CalendarEvent(title=text(EditorField.TITLE), start=start, end=end, timezone=self.timezone,
            all_day=self.all_day, location=self.original.location if self.original else "", command=text(EditorField.COMMAND),
            alarms=self.alarms,
            repeat=Recurrence.from_input(self.unit, text(EditorField.INTERVAL), self.repeat_end,
                                         text(EditorField.REPEAT_COUNT), text(EditorField.REPEAT_UNTIL)),
            notes=text(EditorField.NOTES), holiday=self.holiday)
        return replace(event, id=self.original.id, revision=self.original.revision) if self.original else event

    def edit(self, field, key, data):
        if field.temporal_type is not None:
            return False
        buffer = self.buffer(field)
        if buffer is None:
            return False
        if key in ("<any>", "any", "<bracketed-paste>"):
            text = data if field == EditorField.NOTES else data.replace("\n", " ").replace("\r", " ")
            if text and (key == "<bracketed-paste>" or all(char >= " " for char in text)):
                buffer.save_to_undo_stack()
                buffer.insert_text(text)
        elif key in ("backspace", "c-h"):
            buffer.save_to_undo_stack()
            buffer.delete_before_cursor()
        elif key == "delete":
            buffer.save_to_undo_stack()
            buffer.delete()
        elif key == "left":
            buffer.cursor_left()
        elif key == "right":
            buffer.cursor_right()
        elif key == "up":
            buffer.cursor_up()
        elif key == "down":
            buffer.cursor_down()
        elif key in ("home", "c-a"):
            buffer.cursor_position += buffer.document.get_start_of_line_position()
        elif key in ("end", "c-e"):
            buffer.cursor_position += buffer.document.get_end_of_line_position()
        elif key == "c-u":
            buffer.delete_before_cursor(count=buffer.cursor_position)
        elif key == "c-k":
            buffer.delete(count=len(buffer.text) - buffer.cursor_position)
        elif key == "c-z":
            buffer.undo()
        elif key == "c-y":
            buffer.redo()
        elif key == "enter" and field == EditorField.NOTES:
            buffer.insert_text("\n")
        else:
            return False
        return True
